# -*- coding: utf-8 -*-
"""
htdemucs fp16 ONNX 转换 - Path B: onnxruntime transformers + comprehensive op_block_list + post-fix
策略：
  1. 用 onnxruntime.transformers.float16.convert_float_to_float16 转换
  2. op_block_list 包含所有形状/索引类节点（Range/Shape/Gather/Reshape/Pad/Slice/ScatterND/...）
  3. keep_io_types=True（模型 I/O 保持 fp32，内部 fp16）
  4. 转换后做后处理：遍历所有节点，检查类型不匹配，插入 Cast 修复
  5. onnx.checker 验证 + onnxruntime 推理验证
"""
import os
import sys
import time
import numpy as np
import onnx
from onnx import helper, TensorProto, numpy_helper
from onnxruntime.transformers.float16 import convert_float_to_float16
import onnxruntime as ort

BASE = r"C:\Users\16411\WorkSpace\AndroidStudioProjects\AudioOverlay\docs\demo_audio\separation_results\onnx_export"
FP32_PATH = os.path.join(BASE, "htdemucs.onnx")
FP16_OUT = os.path.join(BASE, "htdemucs_fp16_v2.onnx")

# 形状/索引类节点：这些节点的某些输入必须是 int64 或 fp32，不能是 fp16
# 阻塞它们后，转换器会自动在其输入前插入 Cast(fp16->fp32)
SHAPE_OPS = [
    "Range",           # inputs must be int64 or float32
    "Shape",           # output int64; keep input fp32 to avoid issues
    "Gather",          # indices must be int64
    "Reshape",         # shape input must be int64
    "Pad",             # pads input must be int64
    "Slice",           # starts/ends/steps/axes must be int64
    "ScatterND",       # indices must be int64
    "Unsqueeze",       # axes attribute int64; keep data flow clean
    "ConstantOfShape", # shape input must be int64
    "Expand",          # shape input must be int64
    "Tile",            # repeats must be int64
    "Concat",          # may mix int64/fp16 if concatenating shape tensors
    "Split",           # may involve shape tensors
    "Where",           # condition must be bool
    "Equal",           # output bool; comparison ops
    "Greater",
    "Less",
    "Mod",             # shape-related modulo
    "Resize",          # scales/roi/sizes must be fp32
    "Upsample",
    "TopK",
    "NonMaxSuppression",
    "RoiAlign",
    "CumSum",
    "Min",
    "Max",
    "ArgMax",
    "ArgMin",
    "NonZero",
    "OneHot",
    "IsNaN",
    "IsInf",
]

def log(msg):
    print(msg, flush=True)

def step1_load():
    log("=" * 60)
    log("STEP 1: Load fp32 model")
    t0 = time.time()
    model = onnx.load(FP32_PATH)
    log(f"  loaded in {time.time()-t0:.1f}s")
    log(f"  opset: {model.opset_import[0].version}")
    log(f"  nodes: {len(model.graph.node)}")
    log(f"  initializers: {len(model.graph.initializer)}")
    return model

def step2_convert(model):
    log("=" * 60)
    log("STEP 2: Convert to fp16 with op_block_list")
    log(f"  block list size: {len(SHAPE_OPS)}")
    t0 = time.time()
    try:
        model_fp16 = convert_float_to_float16(
            model,
            keep_io_types=True,
            op_block_list=SHAPE_OPS,
            disable_shape_infer=False,
        )
        log(f"  converted in {time.time()-t0:.1f}s")
        return model_fp16
    except Exception as e:
        log(f"  CONVERSION FAILED: {e}")
        import traceback
        traceback.print_exc()
        return None

def step3_checker(model):
    log("=" * 60)
    log("STEP 3: onnx.checker validation")
    try:
        onnx.checker.check_model(model)
        log("  checker PASSED")
        return True
    except Exception as e:
        log(f"  checker FAILED: {e}")
        return False

def step4_shape_infer(model):
    """Run shape inference to get accurate types for post-processing."""
    log("=" * 60)
    log("STEP 4: Shape inference for post-processing")
    try:
        model = onnx.shape_inference.infer_shapes(model)
        log("  shape inference done")
        return model
    except Exception as e:
        log(f"  shape inference failed: {e}")
        return model

def get_tensor_type(model, name):
    """Get the data type of a tensor by name from value_info, inputs, outputs, or initializers."""
    # Check graph inputs
    for inp in model.graph.input:
        if inp.name == name:
            return inp.type.tensor_type.elem_type
    # Check graph outputs
    for out in model.graph.output:
        if out.name == name:
            return out.type.tensor_type.elem_type
    # Check value_info
    for vi in model.graph.value_info:
        if vi.name == name:
            if vi.type.HasField("tensor_type"):
                return vi.type.tensor_type.elem_type
    # Check initializers
    for init in model.graph.initializer:
        if init.name == name:
            return init.data_type
    return None

def step5_post_fix(model):
    """
    Post-processing: find nodes with fp16 inputs that shouldn't have them,
    insert Cast(fp16->fp32) nodes.
    """
    log("=" * 60)
    log("STEP 5: Post-fix type mismatches")

    # Ops that require fp32 (not fp16) for ALL inputs
    OPS_REQUIRE_FP32_ALL = {"Range"}
    # Ops that require fp32 for specific inputs (index-based)
    # For most shape ops, the data input can be fp16, only shape/index inputs need int64/fp32
    # But to be safe, we check for actual type mismatches

    graph = model.graph
    nodes_to_add = []
    cast_counter = [0]

    def make_cast_name():
        cast_counter[0] += 1
        return f"postfix_cast_{cast_counter[0]}"

    # Build a map of tensor name -> producing node output index
    # and tensor name -> consuming node input indices
    # We'll use shape-inferred value_info for types

    fixes = 0
    for node in graph.node:
        if node.op_type in OPS_REQUIRE_FP32_ALL:
            for i, inp_name in enumerate(node.input):
                if not inp_name:
                    continue
                dtype = get_tensor_type(model, inp_name)
                if dtype == TensorProto.FLOAT16:
                    # Insert Cast(fp16->fp32) before this input
                    cast_out = f"{node.name}_postfix_in_{i}"
                    cast_node = helper.make_node(
                        "Cast", [inp_name], [cast_out],
                        to=TensorProto.FLOAT,
                        name=make_cast_name()
                    )
                    nodes_to_add.append(cast_node)
                    node.input[i] = cast_out
                    # Add value_info for the cast output
                    vi = helper.make_tensor_value_info(cast_out, TensorProto.FLOAT, None)
                    graph.value_info.append(vi)
                    fixes += 1
                    log(f"  FIX: {node.op_type} '{node.name}' input[{i}]={inp_name} was fp16, inserted Cast->fp32")

    if nodes_to_add:
        graph.node.extend(nodes_to_add)
        log(f"  Total fixes: {fixes}")
    else:
        log("  No fixes needed")

    return model

def step6_save(model):
    log("=" * 60)
    log("STEP 6: Save fp16 model")
    t0 = time.time()
    onnx.save(model, FP16_OUT)
    size_mb = os.path.getsize(FP16_OUT) / (1024 * 1024)
    log(f"  saved in {time.time()-t0:.1f}s")
    log(f"  file: {FP16_OUT}")
    log(f"  size: {size_mb:.1f} MB")
    return size_mb

def step7_verify(size_mb):
    log("=" * 60)
    log("STEP 7: Inference verification (fp32 vs fp16)")

    # Use real audio: original.wav first 343980 samples
    import soundfile as sf
    wav_path = r"C:\Users\16411\WorkSpace\AndroidStudioProjects\AudioOverlay\docs\demo_audio\original.wav"
    data, sr = sf.read(wav_path, dtype="float32", always_2d=True)
    if data.shape[1] > 1:
        data = data.mean(axis=1, keepdims=True)
    n = min(343980, data.shape[0])
    mono = np.ascontiguousarray(data[:n, 0], dtype=np.float32)
    stereo = np.stack([mono, mono], axis=0)  # [2, n]
    x = stereo[np.newaxis, :, :]  # [1, 2, n]
    if n < 343980:
        # pad to 343980
        pad = np.zeros((1, 2, 343980), dtype=np.float32)
        pad[:, :, :n] = x
        x = pad
    log(f"  input shape: {x.shape}, dtype: {x.dtype}")

    # fp32 inference
    log("  Running fp32 inference...")
    so_fp32 = ort.SessionOptions()
    sess_fp32 = ort.InferenceSession(FP32_PATH, sess_options=so_fp32, providers=["CPUExecutionProvider"])
    input_name = sess_fp32.get_inputs()[0].name
    t0 = time.time()
    out_fp32 = sess_fp32.run(None, {input_name: x})[0]
    t_fp32 = time.time() - t0
    log(f"  fp32 inference: {t_fp32:.2f}s, output shape: {out_fp32.shape}")

    # fp16 inference
    log("  Running fp16 inference...")
    so_fp16 = ort.SessionOptions()
    sess_fp16 = ort.InferenceSession(FP16_OUT, sess_options=so_fp16, providers=["CPUExecutionProvider"])
    input_name16 = sess_fp16.get_inputs()[0].name
    t0 = time.time()
    out_fp16 = sess_fp16.run(None, {input_name16: x})[0]
    t_fp16 = time.time() - t0
    log(f"  fp16 inference: {t_fp16:.2f}s, output shape: {out_fp16.shape}")

    # Metrics
    log("-" * 40)
    log("  Metrics:")
    diff = out_fp32.astype(np.float64) - out_fp16.astype(np.float64)
    mse = np.mean(diff ** 2)
    max_abs = np.max(np.abs(diff))

    # Cosine similarity (flatten)
    a = out_fp32.flatten().astype(np.float64)
    b = out_fp16.flatten().astype(np.float64)
    cos_sim = np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12)

    # Vocal stem SNR (stem index 3 = vocals)
    vocals_fp32 = out_fp32[0, 3]  # [2, 343980]
    vocals_fp16 = out_fp16[0, 3]
    signal_power = np.mean(vocals_fp32.astype(np.float64) ** 2)
    noise_power = np.mean((vocals_fp32.astype(np.float64) - vocals_fp16.astype(np.float64)) ** 2)
    snr_vocals = 10 * np.log10(signal_power / (noise_power + 1e-12) + 1e-12)

    log(f"  MSE: {mse:.6e}")
    log(f"  Cosine similarity: {cos_sim:.8f}")
    log(f"  Max absolute error: {max_abs:.6e}")
    log(f"  Vocal stem SNR: {snr_vocals:.2f} dB")
    log(f"  fp32 time: {t_fp32:.2f}s")
    log(f"  fp16 time: {t_fp16:.2f}s")
    log(f"  speed ratio: {t_fp32/t_fp16:.2f}x")

    return {
        "mse": mse,
        "cosine": cos_sim,
        "max_abs": max_abs,
        "snr_vocals": snr_vocals,
        "t_fp32": t_fp32,
        "t_fp16": t_fp16,
        "size_mb": size_mb,
    }

def main():
    log("htdemucs fp16 ONNX Conversion - Path B")
    log(f"Python: {sys.executable}")
    log(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    model = step1_load()
    if model is None:
        log("FAILED: cannot load model")
        return 1

    model_fp16 = step2_convert(model)
    if model_fp16 is None:
        log("FAILED: conversion failed")
        return 1

    # First checker attempt
    passed = step3_checker(model_fp16)

    if not passed:
        # Shape inference + post-fix
        model_fp16 = step4_shape_infer(model_fp16)
        model_fp16 = step5_post_fix(model_fp16)
        # Re-check
        passed = step3_checker(model_fp16)

    if not passed:
        log("FAILED: checker still failing after post-fix")
        # Save anyway for debugging
        step6_save(model_fp16)
        return 1

    size_mb = step6_save(model_fp16)

    try:
        metrics = step7_verify(size_mb)
        log("=" * 60)
        log("SUCCESS!")
        log(f"  Path: B (onnxruntime transformers + op_block_list + post-fix)")
        log(f"  Model: {FP16_OUT}")
        log(f"  Size: {metrics['size_mb']:.1f} MB")
        log(f"  MSE: {metrics['mse']:.6e}")
        log(f"  Cosine: {metrics['cosine']:.8f}")
        log(f"  Max abs: {metrics['max_abs']:.6e}")
        log(f"  Vocal SNR: {metrics['snr_vocals']:.2f} dB")
        log(f"  fp32 time: {metrics['t_fp32']:.2f}s, fp16 time: {metrics['t_fp16']:.2f}s")
        return 0
    except Exception as e:
        log(f"VERIFICATION FAILED: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""htdemucs fp16 ONNX 精度验证：fp32 vs 修复后 fp16，输出对比 + 计时 + 体积"""
import os, time, sys
import numpy as np
import onnxruntime as ort

BASE = r"C:\Users\16411\WorkSpace\AndroidStudioProjects\AudioOverlay\docs\demo_audio\separation_results\onnx_export"
F32 = os.path.join(BASE, "htdemucs.onnx")
F16 = os.path.join(BASE, "htdemucs_fp16_ok.onnx")
RESULT = os.path.join(BASE, "fp16_verify_result.txt")

# 固定随机种子，幅度 0.1
rng = np.random.default_rng(0)
x = (rng.standard_normal((1, 2, 343980)) * 0.1).astype(np.float32)

def load_and_run(path, n_warmup=1, n_run=3):
    """加载模型，warmup 后跑 n_run 次取平均，返回 (output, avg_ms)"""
    so = ort.SessionOptions()
    so.intra_op_num_threads = 4
    so.log_severity_level = 3
    sess = ort.InferenceSession(path, so, providers=["CPUExecutionProvider"])
    iname = sess.get_inputs()[0].name
    # warmup
    for _ in range(n_warmup):
        sess.run(None, {iname: x})
    # timed runs
    times = []
    for _ in range(n_run):
        t0 = time.time()
        y = sess.run(None, {iname: x})[0]
        times.append((time.time() - t0) * 1000)
    return y, np.mean(times), times

def cosine_similarity(a, b):
    a_flat = a.astype(np.float64).flatten()
    b_flat = b.astype(np.float64).flatten()
    dot = np.dot(a_flat, b_flat)
    na = np.linalg.norm(a_flat)
    nb = np.linalg.norm(b_flat)
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)

def main():
    lines = []
    def log(msg):
        print(msg, flush=True)
        lines.append(msg)

    log("=" * 60)
    log("htdemucs fp16 ONNX 精度验证报告")
    log("=" * 60)
    log(f"输入: shape={x.shape}, dtype={x.dtype}, seed=0, amp=0.1")
    log(f"fp32 模型: {F32}")
    log(f"fp16 模型: {F16}")
    log("")

    # 体积
    size32 = os.path.getsize(F32)
    size16 = os.path.getsize(F16)
    log(f"--- 体积对比 ---")
    log(f"fp32: {size32/1e6:.1f} MB ({size32} bytes)")
    log(f"fp16: {size16/1e6:.1f} MB ({size16} bytes)")
    log(f"压缩比: {size16/size32*100:.1f}% (节省 {(1-size16/size32)*100:.1f}%)")
    log("")

    # 加载 + 推理
    log("--- 加载 fp32 模型并推理 (warmup=1, run=3) ---")
    y32, avg32, times32 = load_and_run(F32)
    log(f"fp32 输出: shape={y32.shape}, dtype={y32.dtype}")
    log(f"fp32 推理耗时: 平均 {avg32:.1f}ms, 各次={[f'{t:.1f}' for t in times32]}")
    log("")

    log("--- 加载 fp16 模型并推理 (warmup=1, run=3) ---")
    y16, avg16, times16 = load_and_run(F16)
    log(f"fp16 输出: shape={y16.shape}, dtype={y16.dtype}")
    log(f"fp16 推理耗时: 平均 {avg16:.1f}ms, 各次={[f'{t:.1f}' for t in times16]}")
    log("")

    # 速度对比
    log("--- 速度对比 ---")
    log(f"fp32 平均: {avg32:.1f}ms")
    log(f"fp16 平均: {avg16:.1f}ms")
    if avg16 > 0:
        log(f"fp16/fp32 耗时比: {avg16/avg32:.2f}x")
        log(f"加速比: {avg32/avg16:.2f}x (fp16 相对 fp32)")
    log("注: CPU 上 fp16 通常无加速甚至更慢（需模拟），QNN HTP 上才有显著加速")
    log("")

    # 精度对比
    log("--- 精度对比 (fp16 vs fp32) ---")
    diff = np.abs(y32.astype(np.float64) - y16.astype(np.float64))
    max_abs = diff.max()
    mean_abs = diff.mean()
    rms_diff = np.sqrt((diff**2).mean())
    rms32 = np.sqrt((y32.astype(np.float64)**2).mean())
    rms16 = np.sqrt((y16.astype(np.float64)**2).mean())
    rel_err = mean_abs / rms32 if rms32 > 0 else float('inf')
    cos_sim = cosine_similarity(y32, y16)

    log(f"max_abs_diff:  {max_abs:.6e}")
    log(f"mean_abs_diff: {mean_abs:.6e}")
    log(f"rms_diff:      {rms_diff:.6e}")
    log(f"rms_fp32:      {rms32:.6e}")
    log(f"rms_fp16:      {rms16:.6e}")
    log(f"relative_error (mean_abs/rms_fp32): {rel_err:.6e}")
    log(f"cosine_similarity: {cos_sim:.8f}")
    log("")

    # 分 stem 对比（输出 shape [1,4,2,N]，4 个 stem）
    stem_names = ["drums", "bass", "other", "vocals"]
    log("--- 分 stem 精度 ---")
    for s in range(4):
        d_s = diff[0, s]
        y32_s = y32[0, s].astype(np.float64)
        cos_s = cosine_similarity(y32[0, s], y16[0, s])
        rms_s = np.sqrt((y32_s**2).mean())
        rel_s = d_s.mean() / rms_s if rms_s > 0 else 0
        log(f"  stem[{s}] {stem_names[s]}: max_abs={d_s.max():.6e}, "
            f"mean_abs={d_s.mean():.6e}, cos={cos_s:.8f}, rel_err={rel_s:.6e}")
    log("")

    # 结论
    log("=" * 60)
    log("结论")
    log("=" * 60)
    load_ok = True
    shape_ok = (y16.shape == (1, 4, 2, 343980))
    cos_ok = cos_sim > 0.99
    log(f"ORT 加载 fp16: {'PASS' if load_ok else 'FAIL'}")
    log(f"输出 shape [1,4,2,343980]: {'PASS' if shape_ok else 'FAIL'} (实际 {y16.shape})")
    log(f"余弦相似度 > 0.99: {'PASS' if cos_ok else 'FAIL'} ({cos_sim:.8f})")
    log(f"max_abs_diff: {max_abs:.6e}")
    overall = load_ok and shape_ok and cos_ok
    log(f"总体: {'SUCCESS' if overall else 'CHECK'}")
    log("")

    # 写入文件
    with open(RESULT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"\n结果已写入: {RESULT}", flush=True)
    return 0 if overall else 1

if __name__ == "__main__":
    sys.exit(main())

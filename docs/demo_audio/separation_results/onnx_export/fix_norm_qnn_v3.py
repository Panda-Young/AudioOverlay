# -*- coding: utf-8 -*-
"""
QNN + NaN 综合修复 v3：
htdemucs fp16 模型三类数值风险算子统一包 Cast 到 fp32 域计算：
  1. Erf（Gelu 序列）——QNN CPU EP 的 com.microsoft.Gelu 只支持 fp32
  2. LayerNormalization——fp16 下 ORT CPU 输出全 NaN（实测根因）
  3. Softmax——fp16 下易溢出（随 LayerNorm 一并转 fp32 域）
每类：输入前插 Cast(fp32)，输出后插 Cast(fp16)，保留原始输出名。
基于 htdemucs_fp16_ok.onnx，输出 htdemucs_fp16_qnn.onnx。
本地 ORT CPU EP 验证：加载 + 推理 + 输出无 NaN。
"""
import os, time, sys
import numpy as np
import onnx
from onnx import helper, TensorProto
import onnxruntime as ort

BASE = r"C:\Users\16411\WorkSpace\AndroidStudioProjects\AudioOverlay\docs\demo_audio\separation_results\onnx_export"
SRC = os.path.join(BASE, "htdemucs_fp16_ok.onnx")
OUT = os.path.join(BASE, "htdemucs_fp16_qnn.onnx")
WRAP_OPS = {"Erf", "LayerNormalization", "Softmax"}

def set_type(m, name, t):
    g = m.graph
    for vi in g.value_info:
        if vi.name == name:
            vi.type.tensor_type.elem_type = t
            return
    g.value_info.append(helper.make_tensor_value_info(name, t, None))

def main():
    t0 = time.time()
    m = onnx.load(SRC)
    g = m.graph
    print(f"loaded {SRC}, nodes={len(g.node)}", flush=True)

    idx = 0
    new_nodes = []
    replaced = {op: 0 for op in WRAP_OPS}
    for node in g.node:
        if node.op_type not in WRAP_OPS:
            new_nodes.append(node)
            continue
        # 输入必须非空；Softmax 有 1 个输入，LayerNorm 3 个，Erf 1 个
        ins = list(node.input)
        outs = list(node.output)
        if not ins or not ins[0] or not outs or not outs[0]:
            print(f"  skip {node.op_type} '{node.name}' (empty io)", flush=True)
            new_nodes.append(node)
            continue
        # 保留原输入（后续 Cast 接在这些原输入上）——注意：全部输入都过 fp32？
        # LayerNorm 的 scale/bias 也转 fp32（同域计算），输出转回 fp16
        cin_names = []
        for i, inp in enumerate(ins):
            if not inp:
                cin_names.append("")
                continue
            cn = f"qnn3_{node.op_type}_{idx}_in{i}"
            idx += 1
            c = helper.make_node("Cast", [inp], [cn], to=TensorProto.FLOAT, name=cn + "_c")
            new_nodes.append(c)
            set_type(m, cn, TensorProto.FLOAT)
            cin_names.append(cn)
        cout = f"qnn3_{node.op_type}_{idx}_out"
        idx += 1
        nn = helper.make_node(node.op_type, cin_names, [cout], name=node.name)
        for a in node.attribute:
            nn.attribute.append(a)
        new_nodes.append(nn)
        set_type(m, cout, TensorProto.FLOAT)
        for oi, out in enumerate(outs):
            if not out:
                continue
            co = f"qnn3_{node.op_type}_{idx}_c{oi}"
            idx += 1
            c = helper.make_node("Cast", [cout], [out], to=TensorProto.FLOAT16, name=co + "_c")
            new_nodes.append(c)
            set_type(m, out, TensorProto.FLOAT16)
        replaced[node.op_type] += 1
    del g.node[:]
    g.node.extend(new_nodes)
    print(f"replaced: {replaced}", flush=True)

    # 校验重名
    from collections import Counter
    c = Counter()
    for n in g.node:
        for o in n.output:
            c[o] += 1
    dups = {k: v for k, v in c.items() if v > 1}
    if dups:
        print("DUP OUTPUTS:", dict(list(dups.items())[:5]), flush=True)
        return 1

    onnx.save(m, OUT)
    print(f"saved {OUT} ({os.path.getsize(OUT)/1e6:.1f}MB) in {time.time()-t0:.1f}s", flush=True)

    so = ort.SessionOptions()
    so.log_severity_level = 3
    try:
        sess = ort.InferenceSession(OUT, so, providers=["CPUExecutionProvider"])
    except Exception as e:
        print("LOAD FAILED:", str(e)[:400], flush=True)
        return 1
    iname = sess.get_inputs()[0].name
    x = (np.random.default_rng(0).standard_normal((1, 2, 343980)) * 0.1).astype(np.float32)
    t1 = time.time()
    y = sess.run(None, {iname: x})[0]
    dt = (time.time() - t1) * 1000
    nan = int(np.isnan(y).sum())
    inf = int(np.isinf(y).sum())
    print(f"INFERENCE OK: out={y.shape} time={dt:.0f}ms nan={nan} inf={inf}", flush=True)
    print(f"rms={np.sqrt((y.astype(np.float64)**2).mean()):.5f}", flush=True)
    return 0 if (nan == 0 and inf == 0) else 2

if __name__ == "__main__":
    sys.exit(main())

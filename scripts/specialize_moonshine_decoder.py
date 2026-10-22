"""Specialize a hash-checked decoder for a single cached token on CPU.

With one query, every existing key and the new key are causal. Removing the
zero mask and fixing the token dimension lets ORT fold shape bookkeeping.
The original prefill graph and model weights are preserved.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def specialize(model, layers):
    import onnx
    model = copy.deepcopy(model)
    aliases, keep = {}, []
    for node in model.graph.node:
        if node.op_type == 'Add' and 'mask' in node.input:
            if len(node.input) != 2 or len(node.output) != 1:
                raise ValueError('Unexpected causal mask node')
            aliases[node.output[0]] = next(x for x in node.input if x != 'mask')
        else:
            keep.append(node)
    if len(aliases) != layers:
        raise ValueError('Expected exactly one additive causal mask per layer')
    for node in keep:
        for i, name in enumerate(node.input):
            while name in aliases:
                name = aliases[name]
            node.input[i] = name
    if any('mask' in node.input for node in keep):
        raise ValueError('An unrecognized operation still consumes the mask')
    del model.graph.node[:]
    model.graph.node.extend(keep)
    inputs = [x for x in model.graph.input if x.name != 'mask']
    del model.graph.input[:]
    model.graph.input.extend(inputs)
    for value in (*model.graph.input, *model.graph.output, *model.graph.value_info):
        for dim in value.type.tensor_type.shape.dim:
            if dim.dim_param == 'tokens':
                dim.ClearField('dim_param')
                dim.dim_value = 1
    for value in model.graph.input:
        if value.name in ('input_ids', 'positions'):
            value.type.tensor_type.shape.dim[1].dim_value = 1
    onnx.checker.check_model(model)
    return model


def main():
    import numpy as np
    import onnx
    import onnxruntime as ort
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('source', type=Path)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    manifest = json.loads((a.source/'manifest.json').read_text())
    if manifest['format_version'] != 1 or a.out.exists():
        p.error('Requires format 1 artifacts and a new output directory')
    for filename, expected in manifest['files'].items():
        if Path(filename).name != filename or digest(a.source/filename) != expected:
            raise ValueError('Invalid source artifact: '+filename)
    a.out.mkdir(parents=True)
    for filename in manifest['files']:
        shutil.copyfile(a.source/filename, a.out/filename)
    options = ort.SessionOptions()
    options.intra_op_num_threads = 4
    options.inter_op_num_threads = 1
    options.add_session_config_entry('session.intra_op.allow_spinning', '0')
    checks = []
    for precision in ('float', 'int8'):
        source = a.source/('step-'+precision+'.onnx')
        target = a.out/('single-'+precision+'.onnx')
        onnx.save(specialize(onnx.load(source), manifest['layers']), target)
        original = ort.InferenceSession(str(source), sess_options=options, providers=['CPUExecutionProvider'])
        single = ort.InferenceSession(str(target), sess_options=options, providers=['CPUExecutionProvider'])
        rng = np.random.default_rng(41)
        maximum = 0.
        for old, frames in ((0, 1), (1, 25), (7, 50), (31, 300), (127, 600)):
            shape = (manifest['layers'], 1, manifest['heads'], old, manifest['head_dim'])
            cross_shape = (*shape[:3], frames, shape[-1])
            feeds = dict(input_ids=rng.integers(0, 32000, (1, 1), dtype=np.int64),
                positions=np.array([[old]], dtype=np.int64),
                past_keys=rng.normal(0, .1, shape).astype(np.float32),
                past_values=rng.normal(0, .1, shape).astype(np.float32),
                cross_keys=rng.normal(0, .1, cross_shape).astype(np.float32),
                cross_values=rng.normal(0, .1, cross_shape).astype(np.float32))
            expected = original.run(None, {**feeds, 'mask': np.zeros((1, 1, 1, old+1), np.float32)})
            actual = single.run(None, feeds)
            for x, y in zip(actual, expected):
                np.testing.assert_allclose(x, y, rtol=3e-3, atol=3e-4)
            np.testing.assert_array_equal(actual[0].argmax(-1), expected[0].argmax(-1))
            maximum = max(maximum, float(np.max(np.abs(actual[0]-expected[0]))))
        manifest['files'][target.name] = digest(target)
        checks.append(dict(precision=precision, cases=5, maximum_logit_error=maximum))
    manifest['single_token_specialization'] = {
        'source_manifest_sha256': digest(a.source/'manifest.json'), 'checks': checks,
        'method': 'Fixed one-query dimension; remove zero causal-mask additions only'}
    (a.out/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print(json.dumps(manifest['single_token_specialization'], indent=2))


if __name__ == '__main__':
    main()

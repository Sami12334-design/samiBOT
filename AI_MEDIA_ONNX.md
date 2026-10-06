
# Local ONNX stage

The detector can optionally run an ONNX model locally on CPU before using
Hugging Face. ONNX Runtime officially supports CPU execution on Linux x64 and
ARM64, so this is suitable for a low-spec VPS when the model itself is small.

No model weights are committed to SamiBOT because model licenses and file sizes
vary. Install or mount a model on the server and set:

AI_DETECT_ONNX_IMAGE_MODEL=/models/image-detector.onnx
AI_DETECT_ONNX_AUDIO_MODEL=/models/voice-detector.onnx

Optional:

AI_DETECT_ONNX_THREADS=1
AI_DETECT_ONNX_IMAGE_SIZE=224
AI_DETECT_ONNX_IMAGE_MEAN=0.485,0.456,0.406
AI_DETECT_ONNX_IMAGE_STD=0.229,0.224,0.225
AI_DETECT_ONNX_LABELS=real,ai

The image adapter supports common rank-4 vision classifiers. The audio adapter
only accepts raw-waveform rank-2 models. It deliberately refuses models that
need MFCC/mel-spectrogram/model-specific preprocessing instead of silently
feeding them incorrect tensors.

This design follows ONNX Runtime's CPUExecutionProvider model and the
Hugging Face Optimum pattern for ONNX image/audio classification.

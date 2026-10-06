# AI Media Detection

SamiBOT uses a zero-cost waterfall for image and voice screening.

1. Metadata and provenance: EXIF, PNG text chunks, known generator signatures and optional C2PA Reader validation.
2. Local signals: FFT/Laplacian/entropy for images; spectral flatness and silence statistics for audio.
3. Optional free Hugging Face inference: only for borderline local results and only when HF_TOKEN is configured.
4. Fusion: normalized 0-100 AI-likelihood score with evidence and uncertainty warnings.

The score is a screening score, not a probability of truth and not an authenticity certificate.

## Optional configuration

- HF_TOKEN: free Hugging Face token with inference permission.
- HF_IMAGE_MODEL: community image-classification model.
- HF_AUDIO_MODEL: community audio-classification model.
- AI_DETECT_MAX_BYTES: default 15 MB.
- AI_DETECT_BORDERLINE_LOW/HIGH: default 35/65.
- AI_DETECT_HF_TIMEOUT: default 20 seconds.
- AI_DETECT_HF_COOLDOWN: default 30 seconds.

If external inference is unavailable or rate-limited, the detector remains functional locally.

C2PA validation is enabled when c2pa-python is installed. If it is unavailable, safe byte-level C2PA marker detection remains available.

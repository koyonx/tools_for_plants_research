# models/

Docker maps this directory at `/models` (read-only) inside the backend.
Weights and checkpoints live here; everything except this README is
gitignored.

## SegFormer

The backend looks for a fine-tuned SegFormer checkpoint at
`${PLANTS_SEGFORMER_DIR}` (defaults to `/models/segformer`).  Drop a
HuggingFace-format directory there:

```
models/segformer/
├── config.json
├── model.safetensors        # or pytorch_model.bin
├── preprocessor_config.json
└── training_args.bin        # optional
```

You can produce that directory by running
`notebooks/segformer_train.ipynb` against a `training/export.zip`
downloaded from the UI.

If no checkpoint is present, `POST /images/{id}/analyze/segformer`
responds with 503 and the frontend surfaces a friendly message — the
rest of the app (Cellpose, basic measurement) keeps working.

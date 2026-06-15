# Fabric feasibility — medium baselines & dynamic sizing

## medium_size_baseline_fabric_meters

Built-in dress styles store **medium-size** fabric ranges in `app/services/fabric_baselines.py`.
These values are for a **medium-sized person** and are the foundation for dynamic adjustments.

Each entry has `min_meters`, `max_meters`, and `medium_baseline_meters` (midpoint of the range).

## Dynamic adjustment

`POST /ai/feasibility-analysis` and `POST /ai/generate-dress`:

1. Resolve dress type from selected templates, labels, user text, or Gemini Vision (reference image).
2. Sum baselines when multiple garments are selected.
3. Detect size from prompt (S/M/L/XL, plus-sized, measurements).
4. Apply multiplier (L=1.25×, XL=1.5×, XXL=1.75×) or ask **Groq** when measurements are present.
5. Compare `minimum_fabric_required` to uploaded fabric lengths.

## Reference images

Gemini identifies **dress_type / silhouette only**. Generation prompts exclude fabric, color, embroidery, and lace from the reference.

## API

- `GET /ai/fabric-baselines` — full lookup table
- `POST /ai/feasibility-analysis` — analysis only
- `POST /ai/generate-dress` — analysis + `gemini_generation_prompt` for reference mode

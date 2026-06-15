"""
design_descriptions.py

Text descriptions for every built-in garment style shown in the Design Studio.
These are sent to Gemini instead of images, so the feasibility prompt is purely text-based.

Keys match the `id` field in the frontend's BUILTIN_DESIGNS constant.
"""

DESIGN_DESCRIPTIONS: dict[str, str] = {
    "fusion": (
        "Shirt & Plazo: A two-piece outfit consisting of a mid-thigh to knee-length shirt "
        "(kameez) paired with wide-leg palazzo trousers. The shirt has full sleeves and is "
        "usually straight-cut or slightly flared. Requires approximately 3–3.5 metres for "
        "the shirt and 1.5–2 metres for the palazzo, totalling around 5 metres of 44-inch "
        "fabric."
    ),
    "bridal": (
        "Bridal Frock: A floor-length heavily embellished A-line or ball-gown silhouette. "
        "Has a fitted bodice and a full flared skirt that may be layered with net or organza. "
        "Typically requires 3 metres for the bodice and upper portion; the skirt alone can "
        "consume 4–6 metres depending on flare. Total fabric needed: 6–9 metres."
    ),
    "casual": (
        "Simple Kurta: A straight or slightly A-line knee-length tunic with simple seams, "
        "round or V-neck, and three-quarter or full sleeves. Minimal embellishment. One of "
        "the least fabric-intensive garments. Requires approximately 2.5 metres of 44-inch "
        "fabric."
    ),
    "anarkali": (
        "Anarkali: A long flared frock-style kurta that falls to the ankles or floor, "
        "inspired by Mughal-era court dress. Features a fitted bodice with a dramatically "
        "flared skirt starting from the waist or empire line. Typically worn with a churidar "
        "or straight trouser. Requires 4–5 metres for the anarkali plus 2–2.5 metres for "
        "the bottom, totalling 6–7 metres."
    ),
    "lehenga": (
        "Lehenga: A three-piece outfit consisting of a short blouse/choli (0.5–1 m), a "
        "heavily gathered or pleated long skirt (4–5 m), and a dupatta (2–2.5 m). "
        "The skirt has very high fabric consumption because of multiple layers and heavy "
        "gathering. Total fabric required: 6–8 metres."
    ),
    "salwar": (
        "Salwar Kameez: The classic Pakistani three-piece: a long straight or A-line shirt "
        "(kameez, 2.5–3 m), straight or tapered salwar trousers (2–2.5 m), and a dupatta "
        "(2–2.5 m). One of the most versatile and fabric-efficient outfits. Total fabric "
        "required: approximately 4–5 metres."
    ),
    "gents-kurta": (
        "Gents Kurta: A men's knee-length or longer straight-cut shirt with a small stand "
        "collar, button placket, and full sleeves. Usually worn with shalwar or trouser. "
        "Requires approximately 3 metres of 44-inch fabric."
    ),
    "gents-suit": (
        "Gents Suit (2-piece): A men's formal or semi-formal two-piece consisting of a "
        "long kurta/shirt and matching straight trouser or shalwar. Both pieces are usually "
        "cut from the same fabric. Requires approximately 5 metres total."
    ),
    "maxi": (
        "Maxi / Gown: A Western-influenced floor-length dress with a fitted or empire bodice "
        "and a gently flared or straight skirt. Can be sleeveless, short-sleeved, or have "
        "full sleeves. Requires 4–5 metres for a standard adult size."
    ),
    "peplum": (
        "Peplum Top: A short fitted top or blouse with a flared ruffle or overskirt attached "
        "at the waist. Usually paired with cigarette pants or a skirt. Requires approximately "
        "2.5–3 metres for the top plus bottom wear."
    ),
    "sharara": (
        "Sharara: A three-piece outfit with a short knee-length shirt (1.5–2 m), very wide "
        "heavily flared split-leg trousers (sharara, 3–3.5 m), and a dupatta (2 m). "
        "The sharara legs are extremely wide and require significant fabric. Total: 5–6 metres."
    ),
    "gharara": (
        "Gharara: Similar to the sharara but the dramatic flare begins at the knee rather "
        "than the hip, giving a two-tiered look. Worn with a knee-length shirt and dupatta. "
        "Requires 2 m for shirt, 3–3.5 m for gharara, 2 m for dupatta. Total: 5–6 metres."
    ),
    "palazzo": (
        "Palazzo Set: Palazzo trousers (very wide-leg, 2–2.5 m) paired with a short or "
        "mid-length top (1.5–2 m). A lighter, more casual alternative to the sharara. "
        "Total fabric required: approximately 4 metres."
    ),
    "straight-pants": (
        "Straight Trouser: Simple straight-cut full-length trousers with a flat front or "
        "elasticated waist. Often paired with a kurta. Requires approximately 2 metres of "
        "fabric."
    ),
    "churidar": (
        "Churidar Pajama: Slim-fit tapered trousers that gather at the ankle in soft folds, "
        "traditionally worn under anarkali or kurta. Requires approximately 2.5 metres."
    ),
    "koti": (
        "Koti / Waistcoat: A sleeveless front-open jacket worn over a kurta or shirt. "
        "Used as an overlay rather than a standalone garment. Requires approximately "
        "1.5–2 metres of fabric."
    ),
    "long-open": (
        "Open-Front Gown (Long Open): A floor-length open-front overlay coat or abaya-style "
        "jacket worn over an inner shirt and trouser. Has full sleeves and an open front "
        "with no buttons. Requires approximately 4 metres."
    ),
    "kids-frock": (
        "Kids Frock: A short A-line or gathered dress for a child (age 3–10). Much smaller "
        "than an adult garment. Requires approximately 1.5–2 metres of fabric depending on "
        "the child's size."
    ),
    "dupatta-heavy": (
        "Heavy Dupatta Set (with shirt): A dupatta-centric outfit where the dupatta is the "
        "focal piece (heavily embroidered or zari-work). Paired with a simple matching shirt "
        "and trouser. Dupatta: 2.5 m; shirt: 1.5 m; trouser: 1.5 m. Total: ~5 metres but "
        "the shirt and trouser are intentionally simple."
    ),
    "sari-blouse": (
        "Sari Blouse: A short fitted blouse (choli) with a deep back, short sleeves, and "
        "hook-and-eye closure, worn under a sari. Requires only 0.75–1 metre of fabric."
    ),
    "prince-suit": (
        "Prince Suit: A men's formal Sherwani-inspired structured coat with a mandarin "
        "collar and straight silhouette, reaching mid-thigh or knee. Often paired with "
        "churidar. Requires 3.5–4.5 metres for the coat and 2 metres for the bottom."
    ),
    "pathani": (
        "Pathani Set: A traditional Afghan/Pathan-inspired men's outfit consisting of a "
        "long loose kameez (shirt) and loose shalwar, both typically in the same fabric. "
        "The kameez has a simple collar and front button placket. "
        "Requires approximately 4 metres total."
    ),
}

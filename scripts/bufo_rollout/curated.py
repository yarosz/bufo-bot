"""Hand-picked starter emojis for days 1-5 of the rollout."""

# Ordered list of (day, filenames) for curated batches.
# Days 1-5 follow Fibonacci: 1, 1, 2, 3, 5 = 12 emojis total.
CURATED_DAYS: list[tuple[int, list[str]]] = [
    (1, ["bufo.png"]),
    (2, ["bufo-ok.png"]),
    (3, ["bufo-hug.png", "bufo-sad.png"]),
    (4, ["bufo-lol.png", "bufo-nod.gif", "bufo-sip.png"]),
    (5, ["bufo-lit.gif", "bufo-shy.gif", "bufo-hmm.png", "bufo-cry.png", "bufo-fab.png"]),
]

# Flat set of all curated filenames for quick lookup.
CURATED_FILES: set[str] = set()
for _, filenames in CURATED_DAYS:
    CURATED_FILES.update(filenames)

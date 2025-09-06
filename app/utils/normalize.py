# app/utils/normalize.py
def norm_employment_type(v: str | None) -> str | None:
    """
    Normalize employment type to canonical English values:
      - full_time, part_time, internship, freelance
    """
    if not v:
        return None
    s = str(v).strip().lower()

    mapping = {
        # Arabic labels
        "دوام كامل": "full_time",
        "دوام جزئي": "part_time",
        "تدريب": "internship",
        "عمل حر": "freelance",

        # English canonical + synonyms
        "full_time": "full_time", "full-time": "full_time", "full time": "full_time",
        "part_time": "part_time", "part-time": "part_time", "part time": "part_time",
        "intern": "internship", "internship": "internship",
        "freelance": "freelance", "contract": "freelance", "gig": "freelance",
    }
    return mapping.get(s, s)

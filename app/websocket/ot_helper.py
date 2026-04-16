"""
Operational Transformation (OT) Engine
=======================================

Supports three operation types:
  - insert  : { type: "insert",  position: int, content: str }
  - delete  : { type: "delete",  position: int, length:  int }
  - replace : { type: "replace", position: int, length:  int, content: str }

Transform rules (based on Jupiter / Google Wave OT):
  transform(op_a, op_b) → op_a'  such that  apply(apply(doc, op_b), op_a') == apply(apply(doc, op_a), op_b')
"""

from typing import Optional, Tuple


# ─────────────────────────────────────────────
# Apply operations to text
# ─────────────────────────────────────────────

def apply_operation(content: str, op: dict) -> str:
    """Apply a single operation to content string. Raises ValueError on invalid op."""
    op_type = op.get("type") or op.get("operation_type")
    pos = op.get("position", 0)
    pos = max(0, min(pos, len(content)))

    if op_type == "insert":
        text = op.get("content", "")
        return content[:pos] + text + content[pos:]

    elif op_type == "delete":
        length = op.get("length", 0)
        end = min(pos + length, len(content))
        return content[:pos] + content[end:]

    elif op_type == "replace":
        length = op.get("length", 0)
        text = op.get("content", "")
        end = min(pos + length, len(content))
        return content[:pos] + text + content[end:]

    elif op_type == "full_replace":
        # Special: replace entire document content (used for paste / bulk edits)
        return op.get("content", content)

    else:
        raise ValueError(f"Unknown operation type: {op_type!r}")


# ─────────────────────────────────────────────
# Transform pairs
# ─────────────────────────────────────────────

def _op_end(op: dict) -> int:
    """Return the last index affected by an op (exclusive)."""
    t = op.get("type") or op.get("operation_type")
    pos = op.get("position", 0)
    if t == "insert":
        return pos
    elif t in ("delete", "replace"):
        return pos + op.get("length", 0)
    return pos


def transform_insert_vs_insert(op_a: dict, op_b: dict) -> dict:
    """Transform insert-A against insert-B (already applied)."""
    a = dict(op_a)
    if op_b["position"] <= a["position"]:
        a["position"] += len(op_b.get("content", ""))
    return a


def transform_insert_vs_delete(op_a: dict, op_b: dict) -> dict:
    """Transform insert-A against delete-B (already applied)."""
    a = dict(op_a)
    b_pos = op_b["position"]
    b_len = op_b.get("length", 0)

    if b_pos + b_len <= a["position"]:
        # Delete was entirely before our insert point
        a["position"] -= b_len
    elif b_pos < a["position"]:
        # Delete overlaps our insert point — clamp to deletion start
        a["position"] = b_pos

    return a


def transform_delete_vs_insert(op_a: dict, op_b: dict) -> dict:
    """Transform delete-A against insert-B (already applied)."""
    a = dict(op_a)
    b_pos = op_b["position"]
    b_len = len(op_b.get("content", ""))

    if b_pos <= a["position"]:
        a["position"] += b_len
    elif b_pos < a["position"] + a.get("length", 0):
        # Insert was inside our deletion range — expand delete length
        a["length"] = a.get("length", 0) + b_len

    return a


def transform_delete_vs_delete(op_a: dict, op_b: dict) -> dict:
    """Transform delete-A against delete-B (already applied)."""
    a = dict(op_a)
    b_pos = op_b["position"]
    b_len = op_b.get("length", 0)
    a_pos = a["position"]
    a_len = a.get("length", 0)

    b_end = b_pos + b_len
    a_end = a_pos + a_len

    if b_end <= a_pos:
        # B entirely before A
        a["position"] -= b_len

    elif b_pos >= a_end:
        # B entirely after A — no change
        pass

    else:
        # Overlapping deletions
        overlap_start = max(a_pos, b_pos)
        overlap_end = min(a_end, b_end)
        overlap = overlap_end - overlap_start

        if b_pos <= a_pos:
            a["position"] = b_pos
        # Shrink A's length by the overlapping and preceding-delete amount
        shrink = min(b_len, b_end - a_pos) if b_pos <= a_pos else overlap
        a["length"] = max(0, a_len - shrink)

    return a


def transform(op_a: dict, op_b: dict) -> dict:
    """
    Transform op_a so it can be applied AFTER op_b has been applied.
    Returns a new (possibly adjusted) op_a.
    """
    t_a = op_a.get("type") or op_a.get("operation_type")
    t_b = op_b.get("type") or op_b.get("operation_type")

    # full_replace operations always win — no transform needed
    if t_a == "full_replace" or t_b == "full_replace":
        return op_a

    if t_a == "insert":
        if t_b == "insert":
            return transform_insert_vs_insert(op_a, op_b)
        elif t_b in ("delete", "replace"):
            return transform_insert_vs_delete(op_a, op_b)

    elif t_a in ("delete", "replace"):
        if t_b == "insert":
            return transform_delete_vs_insert(op_a, op_b)
        elif t_b in ("delete", "replace"):
            result = transform_delete_vs_delete(op_a, op_b)
            if t_a == "replace":
                # Also shift replacement text position (length stays, but position adjusted)
                pass
            return result

    return op_a  # fallback: return unchanged


def transform_against_history(op: dict, history: list, client_version: int) -> dict:
    """
    Transform `op` (submitted at `client_version`) against all server ops
    that occurred after `client_version`.
    """
    ops_to_transform_against = [
        h for h in history if h.get("version", 0) > client_version
    ]

    transformed = op
    for server_op in ops_to_transform_against:
        transformed = transform(transformed, server_op.get("operation", server_op))

    return transformed


# ─────────────────────────────────────────────
# Compose multiple ops into one (for compression)
# ─────────────────────────────────────────────

def can_compose(op_a: dict, op_b: dict) -> bool:
    """Check if two consecutive insert operations can be merged."""
    t_a = op_a.get("type") or op_a.get("operation_type")
    t_b = op_b.get("type") or op_b.get("operation_type")
    if t_a != "insert" or t_b != "insert":
        return False
    # Can merge if B inserts right after A's insert point
    a_end = op_a["position"] + len(op_a.get("content", ""))
    return op_b["position"] == a_end


def compose(op_a: dict, op_b: dict) -> dict:
    """Merge two consecutive insert ops into one."""
    return {
        "type": "insert",
        "position": op_a["position"],
        "content": op_a.get("content", "") + op_b.get("content", ""),
    }

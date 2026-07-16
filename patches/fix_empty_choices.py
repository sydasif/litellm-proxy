#!/usr/bin/env python3
"""Patch the streaming_iterator.py to handle empty choices in chunk."""

import os
import sys

STREAMING_FILE = "/app/.venv/lib/python3.13/site-packages/litellm/llms/anthropic/experimental_pass_through/adapters/streaming_iterator.py"

def patch_streaming_iterator() -> bool:
    with open(STREAMING_FILE, "r") as f:
        src = f.read()

    # Find the line with: is_final_chunk = chunk.choices[0].finish_reason is not None
    # Add guard before it
    old_line = "                is_final_chunk = chunk.choices[0].finish_reason is not None"
    new_lines = """                # Guard against empty choices (upstream sends empty chunk)
                if not chunk.choices:
                    # Skip empty chunks
                    return self._skip_empty_chunk()
                is_final_chunk = chunk.choices[0].finish_reason is not None"""

    if old_line in src:
        src = src.replace(old_line, new_lines)
        # Add the helper method if it doesn't exist
        if "_skip_empty_chunk" not in src:
            # Find the __anext__ method end or a good place to add helper
            helper = '''
    def _skip_empty_chunk(self):
        """Skip empty chunks from upstream."""
        # Return next chunk from queue or continue iteration
        if self.chunk_queue:
            return self.chunk_queue.popleft()
        # Signal to continue loop
        return None

'''
            # Insert before __anext__ method
            insert_marker = "    async def __anext__(self):"
            if insert_marker in src:
                src = src.replace(insert_marker, helper + insert_marker)

        with open(STREAMING_FILE, "w") as f:
            f.write(src)
        print("PATCHED: Added empty choices guard")
        return True
    else:
        print("SKIP: Target line not found (already patched or version mismatch)")
        return False

if __name__ == "__main__":
    ok = patch_streaming_iterator()
    sys.exit(0 if ok else 1)
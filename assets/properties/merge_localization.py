
from pathlib import Path
import re

def main():
    folder = Path(__file__).parent
    output = folder / "LawnStrings_da.txt"

    parts = sorted(
        folder.glob("LawnStrings_part_*_da.txt"),
        key=lambda p: int(re.search(r"part_(\d+)", p.name).group(1))
    )

    if not parts:
        raise FileNotFoundError("No translated part files found.")

    print(f"Found {len(parts)} files:")
    for part in parts:
        print(f"  {part.name}")

    combined = b""

    for part in parts:
        content = part.read_bytes()

        # Remove a UTF-8 BOM if present, but preserve all other bytes.
        if content.startswith(b"\xef\xbb\xbf"):
            content = content[3:]

        combined += content

    output.write_bytes(combined)

    print(f"\nSuccessfully created: {output.name}")
    print(f"Combined {len(parts)} files.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\nERROR: {e}")

    input("\nPress Enter to close...")


import hashlib
from pathlib import Path


def generate_file_hash(file_path: str, algorithm: str = "sha256") -> str:
    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"Image file not found: {file_path}")

    if not path.is_file():
        raise ValueError(f"That path is not a file: {file_path}")

    hasher = hashlib.new(algorithm)

    with open(path, "rb") as f:
        while chunk := f.read(8192):
            hasher.update(chunk)

    return hasher.hexdigest()


def save_hash_to_file(hash_value: str, output_path: str) -> None:
    path = Path(output_path)

    # Make parent folders if they do not exist
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w") as f:
        f.write(hash_value + "\n")


def main():
    print("=== Image Hash Generator ===")

    image_path = input("Enter the file path of the image: ").strip()
    output_path = input("Enter the file path to save the hash: ").strip()

    try:
        hash_value = generate_file_hash(image_path)

        print("\nGenerated SHA-256 hash:")
        print(hash_value)

        save_hash_to_file(hash_value, output_path)
        print(f"\nHash successfully saved to: {output_path}")

    except Exception as e:
        print(f"\nError: {e}")


if __name__ == "__main__":
    main()
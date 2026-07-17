from src.config import get_settings


def main():
    settings = get_settings()
    if settings.database_path.exists():
        settings.database_path.unlink()
        print(f"Removed {settings.database_path}")

    for path in settings.output_dir.glob("*"):
        if path.name == ".gitkeep":
            continue
        if path.is_file():
            path.unlink()
            print(f"Removed {path}")


if __name__ == "__main__":
    main()

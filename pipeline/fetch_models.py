"""Bake model weights at setup time — the generator is static, nothing is
fetched at runtime. Currently: rembg's u2net session (rembg lazily downloads
~170MB of weights to ~/.u2net/ on first use; triggering that download here
keeps every later build fully offline)."""
from rembg import new_session

if __name__ == "__main__":
    new_session("u2net")
    print("fetch_models: u2net weights present")

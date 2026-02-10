from __future__ import annotations


def app_title() -> str:
    return "Hierag Dashboard"


# %%
if __name__ == "__main__":
    assert app_title() == "Hierag Dashboard"
    print("Check Passed")

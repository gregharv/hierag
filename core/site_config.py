from __future__ import annotations

# Site configuration data for crawl/parse pipelines.
SITES = [
    {
        "id": 1,
        "root_url": r"https://www.jea.com",
        "selector": r"#secondary-content > div > div.cb-content-container.cf",
        "name": r"JEA",
        "breadcrumb_selector": r"#secondary-content > nav",
        "split_function": "split_md_sections",
    },
    {
        "id": 2,
        "root_url": r"https://connections",
        "selector": r"#post > div.doc-scrollable.editor-content",
        "name": r"connections",
        "breadcrumb_selector": r"body > section.page_breadcrumb > div > div > div.col-sm-8.col-md-9 > nav > ol",
        "split_function": "split_with_tabs",
    },
]


def get_site_config(site_id: int):
    """Get site configuration by site id."""
    for site in SITES:
        if site["id"] == site_id:
            return site
    return None


# %%
if __name__ == "__main__":
    assert get_site_config(1)["name"] == "JEA"
    assert get_site_config(999) is None
    print("Check Passed")

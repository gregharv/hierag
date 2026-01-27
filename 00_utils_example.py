# ---
# jupyter:
#   jupytext:
#     formats: ipynb,py:percent
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.18.1
#   kernelspec:
#     display_name: crit
#     language: python
#     name: python3
# ---

# %%
# Site configuration data
SITES = [
    {
        'id': 1,
        'root_url': r"https://www.jea.com",
        'selector': r"#secondary-content > div > div.cb-content-container.cf",
        'name': r"JEA",
        'breadcrumb_selector': r'#secondary-content > nav',
        'split_function': 'split_md_sections'  # Use split_md_sections for JEA
    },
    {
        'id': 2,
        'root_url': r"https://connections",
        'selector': r"#post > div.doc-scrollable.editor-content",
        'name': r"connections",
        'breadcrumb_selector': r'body > section.page_breadcrumb > div > div > div.col-sm-8.col-md-9 > nav > ol',
        'split_function': 'split_with_tabs'  # Use split_with_tabs for Connections
    }
]

# Helper function to get site config by id
def get_site_config(site_id):
    """Get site configuration by site_id."""
    for site in SITES:
        if site['id'] == site_id:
            return site
    return None

# %%

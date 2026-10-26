from ._harness import CASES, COMPONENTS, CATEGORIES, RUBRICS, rt_case  # noqa: F401  (fixture re-export)


def pytest_configure(config):
    config.addinivalue_line("markers", "redteam(case_id, component, category, rubrics, status): FACTORY red-team case")
    for name in (*COMPONENTS, *CATEGORIES, *RUBRICS):
        config.addinivalue_line("markers", f"rt_{name}: red-team tag {name}")

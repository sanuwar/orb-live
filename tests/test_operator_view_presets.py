from src.operator_view_presets import list_presets,get_preset
def test_presets_are_stable_relative_and_closed():
    values=list_presets(); assert [x.preset_id for x in values]==["current-blockers","active-incidents","stale-evidence","missing-evidence","recent-activity","recent-imports","fail-closed-status"]
    assert all(x.target_route.startswith("/") and not x.target_route.startswith("//") and x.to_dict()["read_only"] for x in values)
    assert get_preset("active-incidents").to_dict()["permits_automated_action"] is False

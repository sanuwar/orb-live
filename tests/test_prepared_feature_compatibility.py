from scripts.verify_prepared_feature_compatibility import compatibility_report
def test_prepared_feature_compatibility_report_is_closed():
    report=compatibility_report(); assert report["feature_order"]==("freshness","coverage","auto_refresh","saved_views","route_contracts","trends","release_info"); assert report["route_write_authority"] is False and report["network_authority"] is False and report["external_resources"] is False

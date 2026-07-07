from aicg.util import detect_privacy_flags


def test_sensitive_path_detection_ignores_generic_home_paths():
    assert "SENSITIVE_PATH" not in detect_privacy_flags("/Users/ssyuan/work/project/file.py")
    assert "SENSITIVE_PATH" not in detect_privacy_flags("/home/user/work/project/file.py")


def test_sensitive_path_detection_keeps_actual_sensitive_locations():
    assert "SENSITIVE_PATH" in detect_privacy_flags("/Users/ssyuan/.ssh/id_ed25519")
    assert "SENSITIVE_PATH" in detect_privacy_flags("/home/user/.aws/credentials")


def test_raw_payload_risk_threshold_is_not_triggered_by_small_realistic_payloads():
    assert "RAW_PAYLOAD_RISK" not in detect_privacy_flags("x" * 3000)
    assert "RAW_PAYLOAD_RISK" in detect_privacy_flags("x" * 70000)

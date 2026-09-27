from __future__ import annotations

import json
from pathlib import Path

import pytest

from toem_reid.experiment import (
    BudgetError,
    ExperimentConfig,
    SealError,
    check_budget,
    config_sha256,
    load_config,
    register_sealed_test,
)


def _config(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "config_id": "a-sift-original",
        "algorithm_family": "A",
        "model_identifier": "opencv-sift",
        "model_version": "opencv-4.x",
        "preprocessing": "original",
        "max_side": 512,
        "training_config": None,
        "seed": 0,
        "parameters": {"ratio": 0.8},
    }
    data.update(overrides)
    return data


def _write(tmp_path: Path, name: str, **overrides: object) -> Path:
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(_config(**{"config_id": name, **overrides})), encoding="utf-8")
    return path


def test_config_loads_and_hash_is_stable(tmp_path: Path) -> None:
    config = load_config(_write(tmp_path, "a1"))
    assert isinstance(config, ExperimentConfig)
    assert config_sha256(config) == config_sha256(
        load_config(_write(tmp_path, "a1b", config_id="a1"))
    )


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"algorithm_family": "D"}, "algorithm_family must be one of"),
        ({"preprocessing": "segmented"}, "preprocessing must be one of"),
        ({"max_side": 0}, "max_side"),
        ({"seed": "x"}, "seed"),
        ({"model_version": ""}, "model_version"),
        ({"algorithm_family": "C", "training_config": None}, "family C requires training_config"),
    ],
)
def test_invalid_configs_are_refused(
    tmp_path: Path, override: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        load_config(_write(tmp_path, "bad", **override))


def test_budget_allows_three_configs_per_family(tmp_path: Path) -> None:
    configs = [
        load_config(_write(tmp_path, f"a{i}", parameters={"ratio": 0.7 + i / 10})) for i in range(3)
    ]
    check_budget(configs)


def test_budget_refuses_a_fourth_config_in_one_family(tmp_path: Path) -> None:
    configs = [load_config(_write(tmp_path, f"a{i}", parameters={"ratio": i})) for i in range(4)]
    with pytest.raises(BudgetError, match="family A has 4 configurations"):
        check_budget(configs)


def test_budget_refuses_more_than_one_fine_tuned_model_family(tmp_path: Path) -> None:
    configs = [
        load_config(
            _write(
                tmp_path,
                "c1",
                algorithm_family="C",
                model_identifier="arcface-swin",
                training_config={"epochs": 5},
            )
        ),
        load_config(
            _write(
                tmp_path,
                "c2",
                algorithm_family="C",
                model_identifier="triplet-resnet",
                training_config={"epochs": 5},
            )
        ),
    ]
    with pytest.raises(BudgetError, match="at most one fine-tuned model family"):
        check_budget(configs)


def test_sealed_test_allows_identical_rerun_but_refuses_changes(tmp_path: Path) -> None:
    log = tmp_path / "sealed.jsonl"
    register_sealed_test(
        log,
        split_sha256="s" * 64,
        config_sha256="c" * 64,
        calibration_sha256="t" * 64,
        experiment_id="e1",
    )
    register_sealed_test(
        log,
        split_sha256="s" * 64,
        config_sha256="c" * 64,
        calibration_sha256="t" * 64,
        experiment_id="e2",
    )
    with pytest.raises(SealError, match="new test protocol"):
        register_sealed_test(
            log,
            split_sha256="s" * 64,
            config_sha256="c" * 64,
            calibration_sha256="u" * 64,
            experiment_id="e3",
        )
    with pytest.raises(SealError, match="new test protocol"):
        register_sealed_test(
            log,
            split_sha256="s" * 64,
            config_sha256="d" * 64,
            calibration_sha256="t" * 64,
            experiment_id="e4",
        )
    register_sealed_test(
        log,
        split_sha256="n" * 64,
        config_sha256="d" * 64,
        calibration_sha256="u" * 64,
        experiment_id="e5",
    )
    assert len(log.read_text(encoding="utf-8").splitlines()) == 3


def test_sealed_test_allows_each_pre_registered_config_once_per_protocol(tmp_path: Path) -> None:
    log = tmp_path / "sealed.jsonl"
    register_sealed_test(
        log,
        split_sha256="s" * 64,
        config_sha256="a" * 64,
        calibration_sha256="t" * 64,
        experiment_id="e1",
        registered_configs={"a" * 64, "b" * 64},
    )
    register_sealed_test(
        log,
        split_sha256="s" * 64,
        config_sha256="b" * 64,
        calibration_sha256="u" * 64,
        experiment_id="e2",
        registered_configs={"a" * 64, "b" * 64},
    )
    with pytest.raises(SealError, match="new test protocol"):
        register_sealed_test(
            log,
            split_sha256="s" * 64,
            config_sha256="c" * 64,
            calibration_sha256="v" * 64,
            experiment_id="e3",
            registered_configs={"a" * 64, "b" * 64},
        )

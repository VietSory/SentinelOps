import json
from pathlib import Path

import pytest
np=pytest.importorskip("numpy")
pytest.importorskip("sklearn")

from sentinelops.public_data.benchmark import control_scores,fit_controls,point_metrics,valid_matrix


def protocol():
    return json.loads((Path(__file__).resolve().parents[1]/"configs/public_smd_benchmark.json").read_text())


def test_preprocessing_is_fit_only_and_calibration_does_not_redefine_the_model():
    values=np.zeros((1000,38));values[700:]=100.
    bundle,split=fit_controls(values,protocol())
    assert np.array_equal(bundle["scaler"].center_,np.zeros(38))
    assert np.array_equal(bundle["median"],np.zeros(38))
    assert split["fit_index_range"]==[0,700] and split["calibration_index_range"]==[700,1000]
    assert bundle["activation_allowed"] is False and len(bundle["ordered_features"])==38


def test_future_test_values_cannot_modify_frozen_thresholds_or_preprocessing():
    values=np.random.default_rng(42).normal(size=(1000,38))
    bundle,_=fit_controls(values,protocol())
    frozen=json.dumps(bundle["thresholds"],sort_keys=True);center=bundle["scaler"].center_.copy()
    control_scores(bundle,np.ones((10,38))*1000)
    assert json.dumps(bundle["thresholds"],sort_keys=True)==frozen
    assert np.array_equal(bundle["scaler"].center_,center)


def test_evaluation_reports_raw_point_counts_without_adjusting_an_event():
    result=point_metrics([0,0,1,1],[0.,2.,2.,0.],1.)
    assert (result["tn"],result["fp"],result["tp"],result["fn"])==(1,1,1,1)
    assert result["precision"]==result["recall"]==result["f1"]==.5
    assert result["false_positives_per_1000_normal_points"]==500
    assert result["point_adjustment"] is False


@pytest.mark.parametrize("values",[np.zeros((10,5)),np.full((10,38),np.nan)])
def test_public_channels_cannot_be_coerced_into_live_red_inputs(values):
    with pytest.raises(ValueError,match="38 finite"):
        valid_matrix(values)


@pytest.mark.parametrize("labels,scores,threshold",[([0,.5],[0.,1.],.5),([],[],.5),([0,1],[0.,1.],float('nan'))])
def test_evaluation_never_truncates_nonbinary_labels_or_hides_empty_data(labels,scores,threshold):
    with pytest.raises(ValueError): point_metrics(labels,scores,threshold)

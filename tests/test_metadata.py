import json
import polars as pl
import pytest

from intellicage.metadata import folder_labels, group_hint, resolve_labels
from intellicage.pipeline import load_datasets
from intellicage.project import Project, discover


def test_folder_metadata_and_batch_paths_only(config):
    specs = [{'path': ds['path']} for ds in config['datasets']]
    loaded = load_datasets(specs)
    assert [(d.group, d.lot, d.phase) for d in loaded] == [('control', 'lot 1', 'phase 1'), ('ko', 'lot 2', 'phase 1')]
    project = Project({**config, 'datasets': specs})
    assert project.cfg['datasets'][0]['phase'] == 'phase 1'
    # edits in a saved project override the original labels
    changed = resolve_labels({**specs[0], 'group': 'edited', 'phase': 'next phase'})
    assert changed['group'] == 'edited'
    assert changed['phase'] == 'next phase'
    assert changed['lot'] == 'lot 1'


def test_unlabelled_folder_is_not_assigned_an_invented_phase(export_factory):
    folder = export_factory()
    (folder / 'intellicage.json').unlink()
    assert discover(folder) == [{'path': str(folder), 'group': '', 'lot': '', 'phase': ''}]
    with pytest.raises(ValueError, match='Group and phase names are required'):
        load_datasets([{'path': str(folder)}])
    assert group_hint(folder) == ''
    animals = pl.read_csv(folder / 'Animal.txt', separator='\t')
    animals.with_columns(pl.lit('Group 2').alias('Group')).write_csv(folder / 'Animal.txt', separator='\t')
    assert group_hint(folder) == 'Group 2'
    animals.with_columns(pl.Series('Group', ['one', 'two', 'two'])).write_csv(folder / 'Animal.txt', separator='\t')
    assert group_hint(folder) == ''


@pytest.mark.parametrize('content', ['{broken', '[]', '{"group": 12}', '{"phase": null}'])
def test_invalid_sidecar_is_reported(tmp_path, content):
    (tmp_path / 'intellicage.json').write_text(content)
    with pytest.raises(ValueError, match='invalid|must be text'):
        folder_labels(tmp_path)


def test_labels_trim_whitespace_and_reject_non_text(tmp_path):
    (tmp_path / 'intellicage.json').write_text(json.dumps({'group': ' control ', 'phase': ' phase 3 '}))
    assert folder_labels(tmp_path) == {'group': 'control', 'lot': '', 'phase': 'phase 3'}
    with pytest.raises(ValueError, match='must be text'):
        resolve_labels({'path': str(tmp_path), 'group': 5})


def test_saved_project_runs_from_terminal_without_dataset_configuration(config, tmp_path, capsys):
    from intellicage.__main__ import main
    cfg = {**config, 'analyses': [config['analyses'][0]]}
    source = tmp_path / 'study.icage'
    source.write_bytes(Project(cfg).save())
    main([str(source), '--list'])
    assert 'visits_n' in capsys.readouterr().out
    output = main([str(source), '--only', 'visits_n'])
    data = pl.read_csv(output / 'chart_data/mouse_summary_visits_n.csv')
    assert set(data['group']) == {'control', 'ko'}
    assert set(data['phase']) == {'phase 1'}
    assert (output / 'report.html').is_file()

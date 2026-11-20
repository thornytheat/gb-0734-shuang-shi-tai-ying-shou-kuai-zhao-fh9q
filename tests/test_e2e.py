from app.evidence import digest


def test_all_module_routes_after_real_file_upload(client, raw, params):
    reply = client.post('/api/intake', files={'file': ('sample.csv', raw)})
    assert reply.status_code == 200
    source_id = digest(raw)
    assert reply.json()['source_id'] == source_id
    assert client.get(f'/api/intake/{source_id}').json() == reply.json()
    query = {**params, 'source_id': source_id}
    routes = {name: client.get(f'/api/{name}', params=query) for name in ('versions', 'snapshot', 'aging', 'backfill', 'bridge')}
    assert all(value.status_code == 200 for value in routes.values())
    archive = client.post('/api/archive', json=query).json()
    bundle = client.get(f"/api/archive/{archive['manifest']['run_id']}/export").json()['result']
    assert bundle['bridge'] == routes['bridge'].json()
    assert bundle['backfill'] == routes['backfill'].json()
    assert bundle['aging'] == routes['aging'].json()
    assert bundle['closing'] == routes['snapshot'].json()
    assert 'A-V2' in {row['event_id'] for row in routes['versions'].json()['versions']}


def test_home_serves_workbench_and_local_assets(client):
    reply = client.get('/')
    assert reply.status_code == 200 and '双时态应收快照' in reply.text
    assert all(f'data-page="{name}"' in reply.text for name in
               ('intake', 'versions', 'snapshot', 'aging', 'backfill', 'bridge', 'archive', 'restatement'))
    assets = ['theme.css', 'layout.css', 'components.css', 'workbench.js', 'views.js', 'axis.js']
    assert all(client.get(f'/static/{item}').status_code == 200 for item in assets)


def test_restatement_workflow_through_http(client, raw, params):
    source_id = digest(raw)
    run = client.post('/api/archive', json={**params, 'source_id': source_id}).json()['manifest']['run_id']
    supplement = client.get('/api/intake/sample-supplement').json()
    assert client.get('/api/intake').json()['sources']
    restate_id = client.post('/api/restatement', json={
        'parent_run_id': run, 'supplement_source_ids': [supplement['source_id']],
        'restate_known': '2026-03-01T12:00+08:00'}).json()['manifest']['restate_id']
    detail = client.get(f'/api/restatement/{restate_id}')
    assert detail.status_code == 200
    assert [row['role'] for row in detail.json()['sources']] == ['ARCHIVE', 'SUPPLEMENT']
    assert client.post(f'/api/restatement/{restate_id}/rerun').json()['created'] is False
    assert client.get(f'/api/restatement/{restate_id}/export').status_code == 200
    assert client.get('/api/restatement', params={'parent_run_id': run}).json()['restatements']

import {expect, it} from 'vitest';
import {Api, ApiError} from '../../src/settings/api.js';

function response(data: unknown, status = 200) {
  return new Api(async () => new Response(JSON.stringify(data), {status}));
}

it.each([null, {}, {models: null, default: 'm'}, {models: [1], default: 'm'}, {models: ['m'], default: null}])(
  'rejects malformed model responses: %j', async data => {
    await expect(response(data).models()).rejects.toMatchObject({name: 'Error', status: 200,
      message: 'The server returned an invalid response.'});
  });

it('rejects a successful non-JSON body instead of handing null to the UI', async () => {
  const api = new Api(async () => new Response('not JSON', {status: 200}));
  await expect(api.models()).rejects.toBeInstanceOf(ApiError);
});

it('retains forward-compatible fields', async () => {
  const data = {models: ['m'], default: 'm', future: {enabled: true}};
  expect(await response(data).models()).toEqual(data);
  expect(await response({connections: [], current: null}).connections()).toEqual({connections: [], current: null});
});

it('validates the selected Settings responses at the API boundary', async () => {
  expect(await response({host: 'h', name: 'd', schemas: [{name: 's', objects: 1, extra: true}]}).connection()).toMatchObject({host: 'h'});
  expect(await response({errors: [], context_chars: null}).validate({dataset_toml: '', notes_md: '', glossary_md: ''}, [])).toEqual({errors: [], context_chars: null});
  expect(await response({workspaces: [{name: 'demo', connection: 'demo', kind: 'data', future: 1}]}).workspaces()).toMatchObject({workspaces: [{name: 'demo'}]});
  // A registry entry without a login is listed (so it can be deleted), not a broken response.
  expect(await response({workspaces: [{name: 'demo', connection: null, kind: 'data'}]}).workspaces()).toMatchObject({workspaces: [{name: 'demo', connection: null}]});
  await expect(response({workspaces: [{name: 'demo', connection: 1, kind: 'data'}]}).workspaces()).rejects.toBeInstanceOf(ApiError);
  await expect(response({errors: 'bad', context_chars: 0}).validate({dataset_toml: '', notes_md: '', glossary_md: ''}, [])).rejects.toBeInstanceOf(ApiError);
  await expect(response({model: 'm', run_ceiling_usd: '0.5'}).estimate('m', 'low')).rejects.toBeInstanceOf(ApiError);
});

it('retains server refusal reasons but rejects malformed error fields', async () => {
  await expect(response({reasons: ['fix a', 'fix b']}, 422).models()).rejects.toMatchObject({status: 422, reasons: ['fix a', 'fix b']});
  await expect(response({error: {secret: 'untrusted'}, reasons: [1]}, 500).models()).rejects.toMatchObject({status: 500, message: 'HTTP 500', reasons: []});
});

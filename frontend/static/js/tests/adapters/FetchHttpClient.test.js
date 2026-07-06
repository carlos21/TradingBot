import { describe, it, expect, beforeEach, vi } from 'vitest';
import { FetchHttpClient } from '../../adapters/browser/FetchHttpClient.js';

describe('FetchHttpClient adapter', () => {
  beforeEach(() => {
    global.fetch = vi.fn();
  });

  it('GETs JSON and returns parsed body', async () => {
    global.fetch.mockResolvedValue({
      ok: true,
      json: async () => ({ pair: 'MNQ' }),
    });

    const client = new FetchHttpClient('/base');
    const result = await client.get('/api/pair');

    expect(global.fetch).toHaveBeenCalledWith('/base/api/pair');
    expect(result).toEqual({ pair: 'MNQ' });
  });

  it('POSTs JSON body', async () => {
    global.fetch.mockResolvedValue({
      ok: true,
      json: async () => ({ id: 1 }),
    });

    const client = new FetchHttpClient();
    const result = await client.post('/api/lines', { pair: 'MNQ', price: 100 });

    expect(global.fetch).toHaveBeenCalledWith('/api/lines', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pair: 'MNQ', price: 100 }),
    });
    expect(result).toEqual({ id: 1 });
  });

  it('PUTs JSON body', async () => {
    global.fetch.mockResolvedValue({
      ok: true,
      json: async () => ({ updated: true }),
    });

    const client = new FetchHttpClient();
    const result = await client.put('/api/lines/1', { price: 200 });

    expect(global.fetch).toHaveBeenCalledWith('/api/lines/1', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ price: 200 }),
    });
    expect(result).toEqual({ updated: true });
  });

  it('DELETEs and returns null on 204', async () => {
    global.fetch.mockResolvedValue({
      ok: true,
      status: 204,
      json: vi.fn(),
    });

    const client = new FetchHttpClient();
    const result = await client.delete('/api/lines/1');

    expect(global.fetch).toHaveBeenCalledWith('/api/lines/1', { method: 'DELETE' });
    expect(result).toBeNull();
  });

  it('DELETEs and returns JSON on 200', async () => {
    global.fetch.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ deleted: true }),
    });

    const client = new FetchHttpClient();
    const result = await client.delete('/api/lines/1');
    expect(result).toEqual({ deleted: true });
  });

  it('throws on DELETE non-ok response', async () => {
    global.fetch.mockResolvedValue({
      ok: false,
      status: 404,
      statusText: 'Not Found',
    });

    const client = new FetchHttpClient();
    await expect(client.delete('/api/lines/1')).rejects.toThrow('HTTP 404');
  });

  it('throws with empty text when response.text fails', async () => {
    global.fetch.mockResolvedValue({
      ok: false,
      status: 500,
      statusText: 'Error',
      text: async () => { throw new Error('read failed'); },
    });

    const client = new FetchHttpClient();
    await expect(client.get('/api/pair')).rejects.toThrow('HTTP 500: Error');
  });
});

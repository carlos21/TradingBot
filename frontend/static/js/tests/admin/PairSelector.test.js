import { describe, it, expect, beforeEach, vi } from 'vitest';
import { PairSelector } from '../../admin/PairSelector.js';

const PAIRS = ['MNQ', 'ES'];

describe('PairSelector', () => {
  beforeEach(() => {
    document.body.innerHTML = '<select id="pair"></select>';
  });

  it('populates the select with the given pairs', () => {
    const el = document.getElementById('pair');
    new PairSelector({ element: el, pairs: PAIRS });

    const options = Array.from(el.querySelectorAll('option')).map(o => o.value);
    expect(options).toEqual(PAIRS);
  });

  it('renders exactly the instruments it is given', () => {
    const el = document.getElementById('pair');
    new PairSelector({ element: el, pairs: ['NQ', 'YM', 'RTY'] });

    const options = Array.from(el.querySelectorAll('option')).map(o => o.value);
    expect(options).toEqual(['NQ', 'YM', 'RTY']);
  });

  it('renders no options when pairs is empty or missing', () => {
    const el = document.getElementById('pair');
    new PairSelector({ element: el, pairs: [] });
    expect(el.querySelectorAll('option').length).toBe(0);

    new PairSelector({ element: el });
    expect(el.querySelectorAll('option').length).toBe(0);
  });

  it('applies the initial selection and exposes it via value', () => {
    const el = document.getElementById('pair');
    const selector = new PairSelector({ element: el, pairs: PAIRS, selected: 'ES' });

    expect(el.value).toBe('ES');
    expect(selector.value).toBe('ES');
  });

  it('invokes onChange with the new pair when the selection changes', () => {
    const el = document.getElementById('pair');
    const onChange = vi.fn();
    new PairSelector({ element: el, pairs: PAIRS, selected: 'MNQ', onChange });

    el.value = 'ES';
    el.dispatchEvent(new Event('change', { bubbles: true }));

    expect(onChange).toHaveBeenCalledWith('ES');
  });

  it('tolerates a missing element', () => {
    const selector = new PairSelector({ element: null, pairs: PAIRS, selected: 'MNQ' });
    expect(selector.value).toBeNull();
  });
});

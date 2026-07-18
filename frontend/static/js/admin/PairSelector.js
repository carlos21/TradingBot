/**
 * Pair Selector Component
 * Owns a single <select> element for pair selection: populates the options
 * from the configured instruments (injected via `pairs`), holds the current
 * value, and notifies on change. Each tab that filters by pair gets its own
 * instance, so pair state is local to the tab.
 */
export class PairSelector {
  constructor({ element, pairs, selected, onChange }) {
    this.element = element;
    this.onChange = onChange || null;

    if (!this.element) return;

    const options = Array.isArray(pairs) ? pairs : [];
    this.element.innerHTML = options
      .map(pair => `<option value="${pair}">${pair}</option>`)
      .join('');
    if (selected) {
      this.element.value = selected;
    }

    this.element.addEventListener('change', () => {
      if (this.onChange) this.onChange(this.value);
    });
  }

  get value() {
    return this.element ? this.element.value : null;
  }
}

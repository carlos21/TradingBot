import { describe, it, expect, beforeEach } from 'vitest';
import { StreamHealthRenderer } from '../../stream_health/StreamHealthRenderer.js';
import { StreamHealthModel } from '../../stream_health/StreamHealthModel.js';
import { ReadinessProgressCalculator } from '../../stream_health/ReadinessProgressCalculator.js';

function buildHealthDom() {
  document.body.innerHTML = `
    <div id="streamHealthWrapper">
      <div id="streamHealthBar">
        <span id="healthDot"></span>
        <span id="healthState"></span>
        <span id="healthLastBar"></span>
        <span id="healthCached"></span>
        <span id="healthAlertIcon"></span>
        <span id="healthReadyBadge"></span>
        <span id="healthChevron"></span>
        <div id="healthCompactProgress"></div>
      </div>
      <div id="streamHealthPanel">
        <div id="detailState"></div>
        <div id="detailLastBar"></div>
        <div id="detailCached"></div>
        <div id="detailHeartbeat"></div>
        <div id="detailDuplicates"></div>
        <div id="detailGaps"></div>
        <div id="detailTicks"></div>
        <div id="detailBatches"></div>
        <div id="detailPlatform"></div>
        <div id="detailHistory"></div>
        <div id="readinessStepper"></div>
        <div id="readinessPhaseCard"></div>
        <div id="readinessTransitionLog"></div>
        <button id="healthResyncBtn"></button>
        <button id="healthCheckParityBtn"></button>
        <div id="healthRefreshStatus"></div>
      </div>
      <div id="parityModal" class="hidden">
        <span id="parityModalIcon"></span>
        <span id="parityModalSummary"></span>
        <div id="parityModalAllGood"></div>
        <div id="parityModalGaps">
          <tbody id="parityModalGapsBody"></tbody>
        </div>
        <span id="parityModalCheckedAt"></span>
        <button id="parityModalClose"></button>
        <button id="parityModalCloseBtn"></button>
      </div>
    </div>
  `;
}

describe('StreamHealthRenderer', () => {
  beforeEach(() => {
    document.body.innerHTML = '';
  });

  it('constructor returns early when required elements are missing', () => {
    document.body.innerHTML = `<div id="streamHealthWrapper"><div id="streamHealthBar"></div></div>
    `;
    const renderer = new StreamHealthRenderer();
    expect(renderer.wrapper).toBeTruthy();
    expect(renderer.bar).toBeTruthy();
    expect(renderer.panel).toBeFalsy();
    // render should be a no-op
    expect(() => renderer.render(new StreamHealthModel(), [], {})).not.toThrow();
  });

  it('renders compact bar and details for a ready healthy state', () => {
    buildHealthDom();
    const renderer = new StreamHealthRenderer();
    const model = new StreamHealthModel();
    model.updateFromHealth({
      readiness_state: 'READY',
      readiness_reason: 'All systems go',
      readiness_percent: 100,
      last_bar_time: 1710000000,
      bars_cached: 1234,
      heartbeat_age_sec: 5,
      duplicate_count: 0,
      gap_count: 0,
      ticks_received: 9999,
      history_batches: 10,
      platform_connected: true,
    });
    const calc = new ReadinessProgressCalculator();

    renderer.render(model, calc.computeStepper(model), calc.computePhaseDisplay(model));

    expect(document.getElementById('healthState').textContent).toBe('READY');
    expect(document.getElementById('healthCached').textContent).toBe('1,234');
    expect(document.getElementById('detailState').textContent).toBe('READY');
    expect(document.getElementById('detailPlatform').textContent).toBe('Connected');
    expect(document.getElementById('detailHistory').textContent).toBe('Complete ✅');
    expect(document.getElementById('healthReadyBadge').textContent).toBe('READY');
    expect(document.getElementById('healthAlertIcon').classList.contains('hidden')).toBe(true);
  });

  it('hides wrapper when disconnected', () => {
    buildHealthDom();
    const renderer = new StreamHealthRenderer();
    const model = new StreamHealthModel();

    renderer.render(model, [], { title: '' });

    expect(document.getElementById('streamHealthWrapper').classList.contains('hidden')).toBe(true);
  });

  it('shows not-ready badge and warning colors when not ready', () => {
    buildHealthDom();
    const renderer = new StreamHealthRenderer();
    const model = new StreamHealthModel();
    model.updateFromHealth({
      readiness_state: 'WARMING_UP',
      readiness_reason: 'Warming up',
      readiness_percent: 60,
      platform_connected: true,
      heartbeat_age_sec: 2,
    });
    const calc = new ReadinessProgressCalculator();

    renderer.render(model, calc.computeStepper(model), calc.computePhaseDisplay(model));

    expect(document.getElementById('healthReadyBadge').textContent).toBe('NOT READY');
    expect(document.getElementById('healthDot').classList.contains('bg-amber-400')).toBe(true);
    expect(document.getElementById('healthState').classList.contains('text-amber-400')).toBe(true);
  });

  it('renders error colors when alert level is error', () => {
    buildHealthDom();
    const renderer = new StreamHealthRenderer();
    const model = new StreamHealthModel();
    model.updateFromHealth({
      readiness_state: 'DISCONNECTED',
      platform_connected: false,
      readiness_percent: 0,
    });
    // Override state so wrapper isn't hidden (renderer would normally hide on DISCONNECTED)
    model.currentState = 'CONNECTED';
    const calc = new ReadinessProgressCalculator();

    renderer.render(model, calc.computeStepper(model), calc.computePhaseDisplay(model));

    expect(document.getElementById('healthDot').classList.contains('bg-rose-500')).toBe(true);
  });

  it('renders stepper nodes and connectors', () => {
    buildHealthDom();
    const renderer = new StreamHealthRenderer();
    const model = new StreamHealthModel();
    model.currentState = 'READY';
    model.alertLevel = 'ok';
    const calc = new ReadinessProgressCalculator();

    renderer.render(model, calc.computeStepper(model), calc.computePhaseDisplay(model));

    const stepper = document.getElementById('readinessStepper');
    expect(stepper.children.length).toBeGreaterThan(7); // nodes + connectors
    expect(stepper.textContent).toContain('Disconnected');
    expect(stepper.textContent).toContain('Ready');
  });

  it('renders phase card with determinate progress', () => {
    buildHealthDom();
    const renderer = new StreamHealthRenderer();
    const model = new StreamHealthModel();
    model.currentState = 'WARMING_UP';
    const phase = {
      title: 'Warming up indicators...',
      reason: 'Warmup in progress',
      determinate: true,
      current: 30,
      total: 100,
      percent: 30,
    };

    renderer.render(model, [], phase);

    const card = document.getElementById('readinessPhaseCard');
    expect(card.textContent).toContain('Warming up indicators...');
    expect(card.textContent).toContain('30 / 100 (30%)');
  });

  it('renders phase card with indeterminate progress', () => {
    buildHealthDom();
    const renderer = new StreamHealthRenderer();
    const model = new StreamHealthModel();
    model.currentState = 'REFRESHING';
    model.readinessPercent = 45;
    const phase = {
      title: 'Loading historical bars...',
      reason: 'Refreshing',
      determinate: false,
      current: 0,
      total: 0,
      percent: 0,
    };

    renderer.render(model, [], phase);

    const card = document.getElementById('readinessPhaseCard');
    expect(card.textContent).toContain('45%');
  });

  it('renders empty transition log', () => {
    buildHealthDom();
    const renderer = new StreamHealthRenderer();
    const model = new StreamHealthModel();
    model.currentState = 'CONNECTED';

    renderer.render(model, [], { title: 'Ready' });

    expect(document.getElementById('readinessTransitionLog').textContent).toBe('No state changes yet');
  });

  it('renders transition log entries', () => {
    buildHealthDom();
    const renderer = new StreamHealthRenderer();
    const model = new StreamHealthModel();
    model.currentState = 'CONNECTED';
    model.transitionHistory = [
      { timestamp: Date.now(), previousState: 'CONNECTED', state: 'READY', reason: 'All good' },
    ];

    renderer.render(model, [], { title: 'Ready' });

    const log = document.getElementById('readinessTransitionLog');
    expect(log.textContent).toContain('CONNECTED → READY');
    expect(log.textContent).toContain('All good');
  });

  it('disables refresh buttons when platform not connected', () => {
    buildHealthDom();
    const renderer = new StreamHealthRenderer();
    const model = new StreamHealthModel();
    model.currentState = 'CONNECTED';
    model.platformConnected = false;

    renderer.render(model, [], { title: '' });

    expect(document.getElementById('healthResyncBtn').disabled).toBe(true);
    expect(document.getElementById('healthCheckParityBtn').disabled).toBe(true);
  });

  it('enables refresh buttons when connected and not refreshing', () => {
    buildHealthDom();
    const renderer = new StreamHealthRenderer();
    const model = new StreamHealthModel();
    model.currentState = 'READY';
    model.platformConnected = true;

    renderer.render(model, [], { title: '' });

    expect(document.getElementById('healthResyncBtn').disabled).toBe(false);
    expect(document.getElementById('healthCheckParityBtn').disabled).toBe(false);
  });

  it('sets and hides refresh status', () => {
    buildHealthDom();
    const renderer = new StreamHealthRenderer();

    renderer.setRefreshStatus('Loading...');
    const status = document.getElementById('healthRefreshStatus');
    expect(status.classList.contains('hidden')).toBe(false);
    expect(status.textContent).toBe('Loading...');
    expect(status.classList.contains('text-emerald-400')).toBe(true);

    renderer.setRefreshStatus('Failed', true);
    expect(status.classList.contains('text-rose-500')).toBe(true);

    renderer.hideRefreshStatus();
    expect(status.classList.contains('hidden')).toBe(true);
  });

  it('expands and collapses panel', () => {
    buildHealthDom();
    const renderer = new StreamHealthRenderer();

    renderer.expand();
    expect(document.getElementById('streamHealthPanel').classList.contains('hidden')).toBe(false);
    expect(document.getElementById('healthChevron').classList.contains('rotate-180')).toBe(true);
    expect(renderer.isExpanded()).toBe(true);

    renderer.collapse();
    expect(document.getElementById('streamHealthPanel').classList.contains('hidden')).toBe(true);
    expect(document.getElementById('healthChevron').classList.contains('rotate-180')).toBe(false);
    expect(renderer.isExpanded()).toBe(false);
  });

  it('handles missing optional elements gracefully', () => {
    document.body.innerHTML = `
      <div id="streamHealthWrapper">
        <div id="streamHealthBar">
          <span id="healthDot"></span>
          <span id="healthState"></span>
          <span id="healthLastBar"></span>
          <span id="healthCached"></span>
          <span id="healthAlertIcon"></span>
        </div>
        <div id="streamHealthPanel"></div>
      </div>
    `;
    const renderer = new StreamHealthRenderer();
    const model = new StreamHealthModel();
    model.updateFromHealth({ readiness_state: 'READY', platform_connected: true });

    expect(() => renderer.render(model, [], { title: 'Ready' })).not.toThrow();
  });

  it('renders pending stepper states', () => {
    buildHealthDom();
    const renderer = new StreamHealthRenderer();
    const model = new StreamHealthModel();
    model.currentState = 'CUSTOM_STATE';
    model.alertLevel = 'warn';

    renderer.render(model, [{ key: 'CUSTOM_STATE', label: 'Custom', status: 'pending' }], { title: 'Waiting' });

    const stepper = document.getElementById('readinessStepper');
    expect(stepper.textContent).toContain('Custom');
  });

  it('handles missing panel when expanding or collapsing', () => {
    document.body.innerHTML = `
      <div id="streamHealthWrapper">
        <div id="streamHealthBar">
          <span id="healthDot"></span>
          <span id="healthState"></span>
          <span id="healthLastBar"></span>
          <span id="healthCached"></span>
          <span id="healthAlertIcon"></span>
        </div>
      </div>
    `;
    const renderer = new StreamHealthRenderer();
    expect(() => renderer.expand()).not.toThrow();
    expect(() => renderer.collapse()).not.toThrow();
    expect(renderer.isExpanded()).toBeFalsy();
  });

  it('handles missing refresh status element', () => {
    document.body.innerHTML = `
      <div id="streamHealthWrapper">
        <div id="streamHealthBar">
          <span id="healthDot"></span>
          <span id="healthState"></span>
          <span id="healthLastBar"></span>
          <span id="healthCached"></span>
          <span id="healthAlertIcon"></span>
        </div>
        <div id="streamHealthPanel"></div>
      </div>
    `;
    const renderer = new StreamHealthRenderer();
    expect(() => renderer.setRefreshStatus('test')).not.toThrow();
    expect(() => renderer.hideRefreshStatus()).not.toThrow();
  });


  it('formats timestamps with fallback', () => {
    buildHealthDom();
    const renderer = new StreamHealthRenderer();

    expect(renderer._fmtTime(1710000000)).toMatch(/\d{1,2}:\d{2}:\d{2}/);
    expect(renderer._fmtTimeFull(1710000000)).toMatch(/Mar 9.*?11:00:00/);
    const badValue = Symbol('bad');
    expect(renderer._fmtTime(badValue)).toBe('Symbol(bad)');
    expect(renderer._fmtTimeFull(badValue)).toBe('Symbol(bad)');
  });
});
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { StreamHealthPanel } from '../../StreamHealthPanel.js';
import { FakeSocket } from '../fakes/FakeSocket.js';

function buildHealthDom() {
  document.body.innerHTML = `
    <div id="streamHealthWrapper" style="position:fixed;">
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
        <button id="healthCheckParityBtn">Check Parity</button>
        <div id="healthRefreshStatus"></div>
      </div>
      <div id="parityModal" class="hidden">
        <span id="parityModalIcon"></span>
        <span id="parityModalSummary"></span>
        <div id="parityModalAllGood" class="hidden"></div>
        <div id="parityModalGaps" class="hidden">
          <table><tbody id="parityModalGapsBody"></tbody></table>
        </div>
        <span id="parityModalCheckedAt"></span>
        <button id="parityModalClose"></button>
        <button id="parityModalCloseBtn"></button>
      </div>
    </div>
  `;
}

describe('StreamHealthPanel', () => {
  let socket;

  beforeEach(() => {
    document.body.innerHTML = '';
    sessionStorage.clear();
    socket = new FakeSocket();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('returns early when wrapper element is missing', () => {
    document.body.innerHTML = '<div id="streamHealthBar"></div>';
    const panel = new StreamHealthPanel(socket);
    expect(panel.renderer.wrapper).toBeFalsy();
  });

  it('binds socket events on construction', () => {
    buildHealthDom();
    const panel = new StreamHealthPanel(socket);
    expect(socket.handlers.has('health_update')).toBe(true);
    expect(socket.handlers.has('readiness_changed')).toBe(true);
    expect(socket.handlers.has('warmup_progress')).toBe(true);
    expect(socket.handlers.has('phase_started')).toBe(true);
    expect(socket.handlers.has('refresh_result')).toBe(true);
    expect(socket.handlers.has('parity_result')).toBe(true);
  });

  it('renders health_update and auto-expands on warn alert', () => {
    buildHealthDom();
    const panel = new StreamHealthPanel(socket);
    socket.trigger('health_update', {
      readiness_state: 'WARMING_UP',
      readiness_reason: 'Warming up',
      readiness_percent: 60,
      platform_connected: true,
      heartbeat_age_sec: 5,
    });

    expect(panel.model.currentState).toBe('WARMING_UP');
    expect(document.getElementById('streamHealthPanel').classList.contains('hidden')).toBe(false);
    expect(document.getElementById('healthState').textContent).toBe('WARMING_UP');
  });

  it('records readiness_changed transitions', () => {
    buildHealthDom();
    const panel = new StreamHealthPanel(socket);
    socket.trigger('readiness_changed', {
      previous_state: 'CONNECTED',
      state: 'WAITING_FOR_HISTORY',
      reason: 'History needed',
    });

    expect(panel.model.transitionHistory).toHaveLength(1);
    expect(document.getElementById('readinessTransitionLog').textContent).toContain('CONNECTED → WAITING_FOR_HISTORY');
  });

  it('updates warmup progress', () => {
    buildHealthDom();
    const panel = new StreamHealthPanel(socket);
    socket.trigger('health_update', { readiness_state: 'WARMING_UP', readiness_percent: 50 });
    socket.trigger('warmup_progress', { phase: 'warmup', current: 30, total: 100, percent: 30 });

    expect(panel.model.phaseProgress.percent).toBe(30);
    expect(document.getElementById('readinessPhaseCard').textContent).toContain('30 / 100 (30%)');
  });

  it('updates phase started', () => {
    buildHealthDom();
    const panel = new StreamHealthPanel(socket);
    socket.trigger('phase_started', { phase: 'refreshing' });

    expect(panel.model.phase).toBe('refreshing');
  });

  it('toggles panel expansion and saves preference', () => {
    buildHealthDom();
    sessionStorage.clear();
    const panel = new StreamHealthPanel(socket);

    panel.toggle();
    expect(document.getElementById('streamHealthPanel').classList.contains('hidden')).toBe(true);
    expect(sessionStorage.getItem('healthPanelCollapsed')).toBe('true');

    panel.toggle();
    expect(document.getElementById('streamHealthPanel').classList.contains('hidden')).toBe(false);
    expect(sessionStorage.getItem('healthPanelCollapsed')).toBe('false');
  });

  it('restores collapsed preference from sessionStorage', () => {
    buildHealthDom();
    sessionStorage.setItem('healthPanelCollapsed', 'true');
    const panel = new StreamHealthPanel(socket);
    expect(document.getElementById('streamHealthPanel').classList.contains('hidden')).toBe(true);
    expect(panel.userCollapsed).toBe(true);
  });

  it('emits request_refresh when resync clicked', () => {
    buildHealthDom();
    const panel = new StreamHealthPanel(socket);
    document.getElementById('healthResyncBtn').click();

    expect(socket.emissions).toContainEqual({ event: 'request_refresh', payload: {} });
    expect(document.getElementById('healthRefreshStatus').textContent).toBe('Requesting refresh...');
  });

  it('emits request_refresh with days when _doRefresh is called with days', () => {
    buildHealthDom();
    const panel = new StreamHealthPanel(socket);
    panel._doRefresh(30);

    expect(socket.emissions).toContainEqual({ event: 'request_refresh', payload: { days: 30 } });
  });

  it('shows refresh result success', () => {
    buildHealthDom();
    const panel = new StreamHealthPanel(socket);
    socket.trigger('refresh_result', { ok: true });

    expect(document.getElementById('healthRefreshStatus').textContent).toBe('Refresh requested. Waiting for data...');
  });

  it('shows refresh result error', () => {
    buildHealthDom();
    const panel = new StreamHealthPanel(socket);
    socket.trigger('refresh_result', { ok: false, error: 'Rate limited' });

    expect(document.getElementById('healthRefreshStatus').textContent).toBe('Refresh failed: Rate limited');
    expect(document.getElementById('healthRefreshStatus').classList.contains('text-rose-500')).toBe(true);
  });

  it('hides refresh status after timeout', () => {
    buildHealthDom();
    const panel = new StreamHealthPanel(socket);
    socket.trigger('refresh_result', { ok: true });
    expect(document.getElementById('healthRefreshStatus').classList.contains('hidden')).toBe(false);

    vi.advanceTimersByTime(5000);
    expect(document.getElementById('healthRefreshStatus').classList.contains('hidden')).toBe(true);
  });

  it('emits check_parity and disables button on parity click', () => {
    buildHealthDom();
    const panel = new StreamHealthPanel(socket);
    document.getElementById('healthCheckParityBtn').click();

    expect(socket.emissions).toContainEqual({ event: 'check_parity' });
    expect(document.getElementById('healthCheckParityBtn').disabled).toBe(true);
    expect(document.getElementById('healthCheckParityBtn').textContent).toBe('Checking...');
  });

  it('renders parity result all good', () => {
    buildHealthDom();
    const panel = new StreamHealthPanel(socket);
    socket.trigger('parity_result', { all_good: true, summary: 'All good', checked_at: 1710000000 });

    expect(document.getElementById('parityModal').classList.contains('hidden')).toBe(false);
    expect(document.getElementById('parityModalIcon').textContent).toBe('✅');
    expect(document.getElementById('parityModalSummary').textContent).toBe('All good');
    expect(document.getElementById('parityModalAllGood').classList.contains('hidden')).toBe(false);
    expect(document.getElementById('parityModalGaps').classList.contains('hidden')).toBe(true);
    expect(document.getElementById('parityModalCheckedAt').textContent).toContain('Checked at');
  });

  it('renders parity result with gaps', () => {
    buildHealthDom();
    const panel = new StreamHealthPanel(socket);
    socket.trigger('parity_result', {
      all_good: false,
      summary: '2 gaps found',
      checked_at: 1710000000,
      gaps: [
        { start_time: 1710000000, duration_seconds: 300, gap_type: 'missing', details: 'Missing bar', is_market_closed: false },
        { start_time: 1710003600, duration_seconds: 600, gap_type: 'extra', details: 'Extra bar', is_market_closed: true },
      ],
    });

    expect(document.getElementById('parityModalIcon').textContent).toBe('⚠️');
    expect(document.getElementById('parityModalAllGood').classList.contains('hidden')).toBe(true);
    expect(document.getElementById('parityModalGaps').classList.contains('hidden')).toBe(false);
    const body = document.getElementById('parityModalGapsBody');
    expect(body.children.length).toBe(2);
    expect(body.textContent).toContain('Missing');
    expect(body.textContent).toContain('Extra');
    expect(body.textContent).toContain('Closed');
  });

  it('renders parity error', () => {
    buildHealthDom();
    const panel = new StreamHealthPanel(socket);
    socket.trigger('parity_result', { error: 'Backend failure' });

    expect(document.getElementById('parityModal').classList.contains('hidden')).toBe(false);
    expect(document.getElementById('parityModalIcon').textContent).toBe('❌');
    expect(document.getElementById('parityModalSummary').textContent).toBe('Error: Backend failure');
  });

  it('closes parity modal on close button click', () => {
    buildHealthDom();
    const panel = new StreamHealthPanel(socket);
    socket.trigger('parity_result', { all_good: true, summary: 'All good' });

    document.getElementById('parityModalClose').click();
    expect(document.getElementById('parityModal').classList.contains('hidden')).toBe(true);

    socket.trigger('parity_result', { all_good: true, summary: 'All good' });
    document.getElementById('parityModalCloseBtn').click();
    expect(document.getElementById('parityModal').classList.contains('hidden')).toBe(true);

    // Clicking modal background also closes
    socket.trigger('parity_result', { all_good: true, summary: 'All good' });
    document.getElementById('parityModal').dispatchEvent(new MouseEvent('click', { bubbles: true }));
    expect(document.getElementById('parityModal').classList.contains('hidden')).toBe(true);
  });

  it('does not auto-expand when user manually collapsed', () => {
    buildHealthDom();
    const panel = new StreamHealthPanel(socket);
    panel.toggle(); // collapse

    socket.trigger('health_update', {
      readiness_state: 'WARMING_UP',
      readiness_reason: 'Warning',
      platform_connected: true,
    });

    expect(document.getElementById('streamHealthPanel').classList.contains('hidden')).toBe(true);
  });

  it('restores position from sessionStorage', () => {
    buildHealthDom();
    sessionStorage.setItem('healthPanelPos', JSON.stringify({ right: '10px', bottom: '20px' }));
    const panel = new StreamHealthPanel(socket);
    const wrapper = document.getElementById('streamHealthWrapper');

    expect(wrapper.style.right).toBe('10px');
    expect(wrapper.style.bottom).toBe('20px');
    expect(wrapper.style.left).toBe('auto');
  });

  it('saves position after drag', () => {
    buildHealthDom();
    sessionStorage.clear();
    const panel = new StreamHealthPanel(socket);
    const wrapper = document.getElementById('streamHealthWrapper');

    Object.defineProperty(wrapper, 'offsetParent', { value: document.body, configurable: true });
    wrapper.getBoundingClientRect = () => ({ left: 100, top: 100, right: 200, bottom: 200, width: 100, height: 100 });
    document.body.getBoundingClientRect = () => ({ left: 0, top: 0, right: 800, bottom: 600, width: 800, height: 600 });

    const bar = document.getElementById('streamHealthBar');
    bar.dispatchEvent(new MouseEvent('mousedown', { clientX: 100, clientY: 100, bubbles: true }));
    document.dispatchEvent(new MouseEvent('mousemove', { clientX: 95, clientY: 95, bubbles: true }));
    document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));

    expect(sessionStorage.getItem('healthPanelPos')).toBeTruthy();
  });

  it('does not start drag when clicking a button', () => {
    buildHealthDom();
    sessionStorage.clear();
    const panel = new StreamHealthPanel(socket);
    const wrapper = document.getElementById('streamHealthWrapper');

    Object.defineProperty(wrapper, 'offsetParent', { value: document.body, configurable: true });
    wrapper.getBoundingClientRect = () => ({ left: 100, top: 100, right: 200, bottom: 200, width: 100, height: 100 });
    document.body.getBoundingClientRect = () => ({ left: 0, top: 0, right: 800, bottom: 600, width: 800, height: 600 });

    const resyncBtn = document.getElementById('healthResyncBtn');
    resyncBtn.dispatchEvent(new MouseEvent('mousedown', { clientX: 100, clientY: 100, bubbles: true }));
    document.dispatchEvent(new MouseEvent('mousemove', { clientX: 50, clientY: 50, bubbles: true }));
    document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));

    // No drag state changes expected
    expect(sessionStorage.getItem('healthPanelPos')).toBeNull();
  });

  it('renders parity result with mismatch gap type', () => {
    buildHealthDom();
    const panel = new StreamHealthPanel(socket);
    socket.trigger('parity_result', {
      all_good: false,
      summary: 'Mismatch found',
      checked_at: 1710000000,
      gaps: [
        { start_time: 1710000000, duration_seconds: 300, gap_type: 'mismatch', details: 'Mismatch bar', is_market_closed: false },
      ],
    });

    const body = document.getElementById('parityModalGapsBody');
    expect(body.children.length).toBe(1);
    expect(body.textContent).toContain('Mismatch');
  });

  describe('lifecycle visibility gate', () => {
    const readyUpdate = {
      readiness_state: 'READY',
      readiness_reason: 'Indicators warmed',
      readiness_percent: 100,
      platform_connected: true,
    };
    const wrapper = () => document.getElementById('streamHealthWrapper');

    it('stays hidden when inactive even with a READY health update', () => {
      buildHealthDom();
      const panel = new StreamHealthPanel(socket);
      panel.setActive(false);

      socket.trigger('health_update', readyUpdate);

      expect(panel.model.currentState).toBe('READY');
      expect(wrapper().classList.contains('hidden')).toBe(true);
    });

    it('hides immediately when deactivated and shows again on reactivation', () => {
      buildHealthDom();
      const panel = new StreamHealthPanel(socket);

      socket.trigger('health_update', readyUpdate);
      expect(wrapper().classList.contains('hidden')).toBe(false);

      // Stream stops → panel disappears even though the model still says READY.
      panel.setActive(false);
      expect(wrapper().classList.contains('hidden')).toBe(true);

      panel.setActive(true);
      expect(wrapper().classList.contains('hidden')).toBe(false);
    });
  });
});
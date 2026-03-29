export class ControlsView {
  constructor(chartViewer, socket, dataService) {
    this.chartViewer = chartViewer;
    this.socket      = socket;
    this.dataService = dataService;

    // UI elements
    this.toggleBtn   = null;
    this.stepBtn     = null;
    this.prevBtn     = null;
    this.nextBtn     = null;
    this.tfButtons   = [];
  }

  init() {
    this.toggleBtn       = document.getElementById('toggleReplayBtn');
    this.stepBtn         = document.getElementById('stepBarBtn');
    this.prevBtn         = document.getElementById('prevDayBtn');
    this.nextBtn         = document.getElementById('nextDayBtn');
    this.currentDayLabel = document.getElementById('currentDayLabel');
    this.tfButtons       = Array.from(document.querySelectorAll('[data-timeframe]'));

    this.bindReplayEvents();
    this.bindTimeframeEvents();
    this.bindSocketEvents();

    const defaultBtn = document.querySelector(`[data-timeframe="${this.chartViewer.currentTF}"]`);
    if (defaultBtn) this.setActiveTf(defaultBtn);
  }

  bindSocketEvents() {
    this.socket.on('stream_status', ({ playing, live_mode }) => {
      if (!playing) this.toggleBtn.textContent = 'Play';
      if (live_mode) this._applyLiveMode();
    });
  }

  bindReplayEvents() {
    this.toggleBtn.addEventListener('click', () => {
      this.chartViewer.toggleReplay();
      this.toggleBtn.textContent = this.chartViewer.isPlaying ? 'Pause' : 'Play';
    });

    this.stepBtn.addEventListener('click', () => {
      this.chartViewer.stepReplay();
      this.toggleBtn.textContent = 'Play';
    });

    this.prevBtn.addEventListener('click', () => {
      this.chartViewer.jumpToDay(-1);
    });

    this.nextBtn.addEventListener('click', () => {
      this.chartViewer.jumpToDay(1);
    });
  }

  bindTimeframeEvents() {
    this.tfButtons.forEach(btn => {
      btn.addEventListener('click', () => {
        this.setActiveTf(btn);
        const tf = btn.getAttribute('data-timeframe');
        this.chartViewer.changeTimeframe(tf);
      });
    });
  }

  setActiveTf(button) {
    this.tfButtons.forEach(b => b.classList.remove('active'));
    button.classList.add('active');
  }

  updateView() {
    // No-op — kept for compatibility with main.js onDisplay callback
  }

  _applyLiveMode() {
    const replayControls = document.querySelectorAll('.replay-control');
    replayControls.forEach(el => el.style.display = 'none');
    if (this.stepBtn) this.stepBtn.style.display = 'none';

    const liveIndicator = document.getElementById('liveIndicator');
    if (liveIndicator) liveIndicator.classList.remove('hidden');

    this.toggleBtn.textContent = 'Start';
  }
}

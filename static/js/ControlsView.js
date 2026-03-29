export class ControlsView {
  constructor(chartViewer, socket) {
    this.chartViewer = chartViewer;
    this.socket = socket;
  }

  init() {
    this.toggleBtn = document.getElementById('toggleReplayBtn');
    this.stepBtn = document.getElementById('stepBarBtn');
    this.prevBtn = document.getElementById('prevDayBtn');
    this.nextBtn = document.getElementById('nextDayBtn');
    this.tfButtons = Array.from(document.querySelectorAll('[data-timeframe]'));

    this._bindReplayEvents();
    this._bindTimeframeEvents();

    this.socket.on('stream_status', ({ playing, live_mode }) => {
      if (!playing) this.toggleBtn.textContent = 'Play';
      if (live_mode) this._applyLiveMode();
    });

    const defaultBtn = document.querySelector(`[data-timeframe="${this.chartViewer.currentTF}"]`);
    if (defaultBtn) this._setActiveTf(defaultBtn);
  }

  _bindReplayEvents() {
    this.toggleBtn.addEventListener('click', () => {
      this.chartViewer.toggleReplay();
      this.toggleBtn.textContent = this.chartViewer.isPlaying ? 'Pause' : 'Play';
    });

    this.stepBtn.addEventListener('click', () => {
      this.chartViewer.stepReplay();
      this.toggleBtn.textContent = 'Play';
    });

    this.prevBtn.addEventListener('click', () => this.chartViewer.jumpToDay(-1));
    this.nextBtn.addEventListener('click', () => this.chartViewer.jumpToDay(1));
  }

  _bindTimeframeEvents() {
    this.tfButtons.forEach(btn => {
      btn.addEventListener('click', () => {
        this._setActiveTf(btn);
        this.chartViewer.changeTimeframe(btn.getAttribute('data-timeframe'));
      });
    });
  }

  _setActiveTf(button) {
    this.tfButtons.forEach(b => b.classList.remove('active'));
    button.classList.add('active');
  }

  _applyLiveMode() {
    document.querySelectorAll('.replay-control').forEach(el => el.style.display = 'none');
    if (this.stepBtn) this.stepBtn.style.display = 'none';

    const liveIndicator = document.getElementById('liveIndicator');
    if (liveIndicator) liveIndicator.classList.remove('hidden');

    this.toggleBtn.textContent = 'Start';
  }
}

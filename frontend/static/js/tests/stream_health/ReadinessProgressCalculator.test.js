import { describe, it, expect, beforeEach } from 'vitest';
import { ReadinessProgressCalculator } from '../../stream_health/ReadinessProgressCalculator.js';
import { StreamHealthModel } from '../../stream_health/StreamHealthModel.js';

describe('ReadinessProgressCalculator', () => {
  let calc;
  let model;

  beforeEach(() => {
    calc = new ReadinessProgressCalculator();
    model = new StreamHealthModel();
  });

  describe('computeStepper', () => {
    it('marks current state active when healthy', () => {
      model.currentState = 'WARMING_UP';
      model.alertLevel = 'warn';
      const stepper = calc.computeStepper(model);

      expect(stepper).toHaveLength(7);
      expect(stepper[0]).toEqual({ key: 'DISCONNECTED', label: 'Disconnected', status: 'completed' });
      expect(stepper[1]).toEqual({ key: 'CONNECTED', label: 'Connected', status: 'completed' });
      expect(stepper[2]).toEqual({ key: 'WAITING_FOR_HISTORY', label: 'History', status: 'completed' });
      expect(stepper[3]).toEqual({ key: 'REFRESHING', label: 'Refreshing', status: 'completed' });
      expect(stepper[4]).toEqual({ key: 'WARMING_UP', label: 'Warmup', status: 'active' });
      expect(stepper[5]).toEqual({ key: 'READY', label: 'Ready', status: 'pending' });
      expect(stepper[6]).toEqual({ key: 'LIVE', label: 'Live', status: 'pending' });
    });

    it('marks current state error when alertLevel is error', () => {
      model.currentState = 'WAITING_FOR_HISTORY';
      model.alertLevel = 'error';
      const stepper = calc.computeStepper(model);
      expect(stepper[2].status).toBe('error');
    });

    it('marks all states pending for unknown state', () => {
      model.currentState = 'UNKNOWN';
      const stepper = calc.computeStepper(model);
      expect(stepper.every(s => s.status === 'pending')).toBe(true);
    });

    it('maps backend STREAMING state to the LIVE step', () => {
      model.currentState = 'STREAMING';
      model.alertLevel = 'ok';
      const stepper = calc.computeStepper(model);

      expect(stepper[6]).toEqual({ key: 'LIVE', label: 'Live', status: 'active' });
      expect(stepper.slice(0, 6).every(s => s.status === 'completed')).toBe(true);
    });

    it('maps backend STREAMING state to error when alertLevel is error', () => {
      model.currentState = 'STREAMING';
      model.alertLevel = 'error';
      const stepper = calc.computeStepper(model);

      expect(stepper[6]).toEqual({ key: 'LIVE', label: 'Live', status: 'error' });
    });

    it('maps backend DEGRADED state to the LIVE step', () => {
      model.currentState = 'DEGRADED';
      model.alertLevel = 'warn';
      const stepper = calc.computeStepper(model);

      expect(stepper[6]).toEqual({ key: 'LIVE', label: 'Live', status: 'active' });
      expect(stepper.slice(0, 6).every(s => s.status === 'completed')).toBe(true);
    });

    it('maps backend DEGRADED state to error when alertLevel is error', () => {
      model.currentState = 'DEGRADED';
      model.alertLevel = 'error';
      const stepper = calc.computeStepper(model);

      expect(stepper[6]).toEqual({ key: 'LIVE', label: 'Live', status: 'error' });
    });

    it('never leaves all steps pending for any canonical backend state', () => {
      const canonicalStates = [
        'DISCONNECTED',
        'CONNECTED',
        'WAITING_FOR_HISTORY',
        'REFRESHING',
        'WARMING_UP',
        'READY',
        'LIVE',
        'STREAMING',
        'DEGRADED',
      ];

      canonicalStates.forEach((state) => {
        model.currentState = state;
        model.alertLevel = 'ok';
        const stepper = calc.computeStepper(model);
        expect(stepper.some(s => s.status !== 'pending')).toBe(true);
      });
    });
  });

  describe('computePhaseDisplay', () => {
    it('renders refreshing phase', () => {
      model.currentState = 'REFRESHING';
      model.reason = 'Loading bars';
      model.readinessPercent = 40;
      const display = calc.computePhaseDisplay(model);

      expect(display.title).toBe('Loading historical bars...');
      expect(display.determinate).toBe(false);
      expect(display.percent).toBe(40);
      expect(display.reason).toBe('Loading bars');
    });

    it('renders warmup phase as stale when reason is stale', () => {
      model.currentState = 'WARMING_UP';
      model.reason = 'Last bar is 5m old';
      model.readinessPercent = 60;
      const display = calc.computePhaseDisplay(model);

      expect(display.title).toBe('Waiting for fresh market data');
      expect(display.determinate).toBe(false);
      expect(display.percent).toBe(60);
    });

    it('renders warmup phase as determinate when reason is fresh', () => {
      model.currentState = 'WARMING_UP';
      model.reason = 'Warming up';
      model.phaseProgress = { phase: 'warmup', current: 30, total: 100, percent: 30 };
      const display = calc.computePhaseDisplay(model);

      expect(display.title).toBe('Warming up indicators...');
      expect(display.determinate).toBe(true);
      expect(display.current).toBe(30);
      expect(display.total).toBe(100);
      expect(display.percent).toBe(30);
    });

    it('renders degraded state', () => {
      model.currentState = 'DEGRADED';
      model.reason = 'Data quality issue';
      model.readinessPercent = 80;
      const display = calc.computePhaseDisplay(model);

      expect(display.title).toBe('Degraded — waiting for data quality to recover');
      expect(display.determinate).toBe(false);
      expect(display.percent).toBe(80);
    });

    it('renders ready state at 100%', () => {
      model.currentState = 'READY';
      const display = calc.computePhaseDisplay(model);

      expect(display.title).toBe('Ready for live trading');
      expect(display.determinate).toBe(true);
      expect(display.current).toBe(100);
      expect(display.total).toBe(100);
      expect(display.percent).toBe(100);
    });

    it('renders live state at 100%', () => {
      model.currentState = 'LIVE';
      const display = calc.computePhaseDisplay(model);

      expect(display.title).toBe('Live trading active');
      expect(display.determinate).toBe(true);
      expect(display.percent).toBe(100);
    });

    it('renders waiting for history with stale reason', () => {
      model.currentState = 'WAITING_FOR_HISTORY';
      model.reason = 'Stale data';
      model.readinessPercent = 10;
      const display = calc.computePhaseDisplay(model);

      expect(display.title).toBe('Waiting for fresh market data');
      expect(display.determinate).toBe(false);
      expect(display.percent).toBe(10);
    });

    it('renders waiting for history with non-stale reason', () => {
      model.currentState = 'WAITING_FOR_HISTORY';
      model.reason = 'Waiting for history';
      model.readinessPercent = 15;
      const display = calc.computePhaseDisplay(model);

      expect(display.title).toBe('Waiting for historical data...');
      expect(display.determinate).toBe(false);
      expect(display.percent).toBe(15);
    });

    it('renders default state using reason', () => {
      model.currentState = 'CONNECTED';
      model.reason = 'Connected to platform';
      model.readinessPercent = 5;
      const display = calc.computePhaseDisplay(model);

      expect(display.title).toBe('Connected to platform');
      expect(display.determinate).toBe(false);
      expect(display.percent).toBe(5);
    });

    it('renders default fallback when reason is missing', () => {
      model.currentState = 'CONNECTED';
      model.reason = '';
      model.readinessPercent = 5;
      const display = calc.computePhaseDisplay(model);

      expect(display.title).toBe('Initializing...');
    });
  });

  describe('_isStaleReason', () => {
    it('detects stale-related keywords', () => {
      expect(calc._isStaleReason('Data is old')).toBe(true);
      expect(calc._isStaleReason('Stale feed')).toBe(true);
      expect(calc._isStaleReason('Market closed')).toBe(true);
      expect(calc._isStaleReason('Gap detected')).toBe(true);
      expect(calc._isStaleReason('Last bar is 10m old')).toBe(true);
    });

    it('returns false for normal reasons', () => {
      expect(calc._isStaleReason('Loading history')).toBe(false);
      expect(calc._isStaleReason('Warming up')).toBe(false);
      expect(calc._isStaleReason('')).toBe(false);
      expect(calc._isStaleReason(null)).toBe(false);
    });
  });
});

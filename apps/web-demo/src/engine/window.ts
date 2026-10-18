/**
 * classifyWindow: run deterministic-baseline-v1 over one recorded/synthetic window, exactly like the
 * Python port's evidence_service.classifier.classify() (first decision in the window is final; a sensor
 * health failure while monitoring decides rejected_sensor_fault; a candidate still open at the end of
 * the window is decided on what was observed; no candidate -> no_event).
 */
import type { ClassifierThresholds, SafetyLimits } from './config';
import { DT_MS } from './config';
import { BiteDetector, monitorSensorFaultDecision, noEventDecision, type Decision } from './detector';
import { SignalProcessor, type SampleFeatures } from './processor';

export function classifyWindow(
  tension: ArrayLike<number>,
  th: ClassifierThresholds,
  lim: SafetyLimits,
  t0Ms = 0,
): Decision {
  const p = new SignalProcessor(th, lim);
  const d = new BiteDetector(th);
  let last: SampleFeatures | null = null;
  for (let i = 0; i < tension.length; i++) {
    const t = t0Ms + i * DT_MS;
    const s = p.update(t, tension[i], d.inCandidate);
    last = s;
    if (!d.inCandidate && !s.valid) return monitorSensorFaultDecision(s);
    const o = d.update(s);
    if (o.kind === 'decision') return o.decision;
  }
  const tEnd = t0Ms + (tension.length - 1) * DT_MS;
  const forced = d.forceDecision(tEnd);
  return forced ?? noEventDecision(tEnd, th, last);
}

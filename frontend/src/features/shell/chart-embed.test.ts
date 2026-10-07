import { describe, expect, it } from 'vitest';

import { chartLayout, chartTraces } from './chart-embed';

const styles = {
  getPropertyValue: (name: string) =>
    ({
      '--surface-overlay': '#1C222C',
      '--text-primary': '#E8EBF0',
    })[name] ?? '',
} as unknown as CSSStyleDeclaration;

describe('chartLayout', () => {
  it("uses the app's hover label over the figure's own colours (B294)", () => {
    const layout = chartLayout(
      { hoverlabel: { bgcolor: '#EEEEEE', font: { color: '#C6B3F5' } } },
      styles,
    );
    expect(layout.hoverlabel).toMatchObject({
      bgcolor: '#1C222C',
      font: { color: '#E8EBF0' },
    });
  });

  it('lets the plot fill its card or the Expand dialog (B290)', () => {
    const layout = chartLayout(
      { width: 700, height: 420, xaxis: { title: 'Month' } },
      styles,
    );
    expect(layout).not.toHaveProperty('width');
    expect(layout).not.toHaveProperty('height');
    expect(layout).toMatchObject({ autosize: true, xaxis: { title: 'Month' } });
  });
});

describe('chartTraces', () => {
  it('puts the series name inside the themed hover label (B294)', () => {
    const [line, own, pie] = chartTraces([
      { type: 'scatter', name: 'water_gbp', x: [1], y: [2] },
      { type: 'bar', name: 'gas', hovertemplate: '%{y} kWh' },
      { type: 'pie', name: 'share', labels: ['a'], values: [1] },
    ]) as Record<string, unknown>[];
    expect(line.hovertemplate).toBe(
      '<b>%{fullData.name}</b><br>%{x}: %{y}<extra></extra>',
    );
    expect(own.hovertemplate).toBe('%{y} kWh');
    expect(pie).not.toHaveProperty('hovertemplate');
  });
});

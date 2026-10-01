import { expect, it } from 'vitest';
import { attachmentLimitProblem, pastedFileName } from './attachment-limits';

function sized(name: string, bytes: number) {
  const file = new File(['x'], name);
  Object.defineProperty(file, 'size', { value: bytes });
  return file;
}

it('names each file it leaves out, with the limit, before any upload', () => {
  const MB = 1024 * 1024;
  const { accepted, problem } = attachmentLimitProblem(
    [sized('empty.txt', 0), sized('a.pdf', 20 * MB), sized('b.pdf', 30 * MB)],
    [],
  );
  expect(accepted.map((file) => file.name)).toEqual(['a.pdf']);
  expect(problem).toBe(
    '“empty.txt” is empty. “b.pdf” is 30 MB; files can be up to 25 MB.',
  );
  const batch = attachmentLimitProblem(
    [sized('c.pdf', 20 * MB)],
    [{ size_bytes: 90 * MB }],
  );
  expect(batch.accepted).toEqual([]);
  expect(batch.problem).toMatch(/can add up to 100 MB/);
  const many = attachmentLimitProblem(
    [sized('d.txt', 10), sized('e.txt', 10)],
    Array.from({ length: 31 }, () => ({ size_bytes: 1 })),
  );
  expect(many.accepted.map((file) => file.name)).toEqual(['d.txt']);
  expect(many.problem).toBe('A message can carry up to 32 files.');
});

it('gives pasted screenshots distinct readable names and keeps real ones', () => {
  const now = new Date(2026, 8, 28, 21, 4, 5);
  const shot = new File(['x'], 'image.png', { type: 'image/png' });
  expect(pastedFileName(shot, now).name).toBe(
    'Pasted image 2026-09-28 21.04.05.png',
  );
  expect(pastedFileName(shot, now, 1).name).toBe(
    'Pasted image 2026-09-28 21.04.05 (2).png',
  );
  const named = new File(['x'], 'report.pdf', { type: 'application/pdf' });
  expect(pastedFileName(named, now)).toBe(named);
});

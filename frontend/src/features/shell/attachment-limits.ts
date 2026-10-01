import type { AttachmentView } from '../../api/types';

/** The server's attachment limits (application/attachments.py). */
export const ATTACHMENT_LIMITS = {
  fileBytes: 25 * 1024 * 1024,
  fileMegabytes: 25,
  files: 32,
  batchBytes: 100 * 1024 * 1024,
  batchMegabytes: 100,
} as const;

function size(bytes: number) {
  if (bytes >= 1024 * 1024) return `${Math.round(bytes / (1024 * 1024))} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

/**
 * Splits files into those that fit the limits and a sentence naming the
 * rest, so a file is refused with the limit stated, before any upload (U18).
 */
export function attachmentLimitProblem(
  files: readonly File[],
  current: readonly Pick<AttachmentView, 'size_bytes'>[],
): { accepted: File[]; problem: string } {
  const problems: string[] = [];
  const accepted: File[] = [];
  let total = current.reduce((sum, item) => sum + (item.size_bytes ?? 0), 0);
  for (const file of files) {
    if (file.size < 1) {
      problems.push(`“${file.name}” is empty.`);
      continue;
    }
    if (file.size > ATTACHMENT_LIMITS.fileBytes) {
      problems.push(
        `“${file.name}” is ${size(file.size)}; files can be up to ${ATTACHMENT_LIMITS.fileMegabytes} MB.`,
      );
      continue;
    }
    if (current.length + accepted.length >= ATTACHMENT_LIMITS.files) {
      problems.push(
        `A message can carry up to ${ATTACHMENT_LIMITS.files} files.`,
      );
      break;
    }
    if (total + file.size > ATTACHMENT_LIMITS.batchBytes) {
      problems.push(
        `“${file.name}” doesn't fit: files in one message can add up to ${ATTACHMENT_LIMITS.batchMegabytes} MB.`,
      );
      continue;
    }
    total += file.size;
    accepted.push(file);
  }
  return { accepted, problem: problems.join(' ') };
}

const pad = (value: number) => String(value).padStart(2, '0');

/**
 * Browsers name every pasted screenshot "image.png"; give each a readable,
 * distinct name ("Pasted image 2026-09-28 21.04.05.png").
 */
export function pastedFileName(file: File, now: Date, index = 0): File {
  if (file.name && !/^image\.(png|jpe?g|gif|webp|bmp)$/i.test(file.name))
    return file;
  const extension =
    (file.type.split('/')[1] || 'png').replace('jpeg', 'jpg') || 'png';
  const stamp = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ${pad(now.getHours())}.${pad(now.getMinutes())}.${pad(now.getSeconds())}`;
  const name = `Pasted image ${stamp}${index ? ` (${index + 1})` : ''}.${extension}`;
  return new File([file], name, {
    type: file.type,
    lastModified: file.lastModified,
  });
}

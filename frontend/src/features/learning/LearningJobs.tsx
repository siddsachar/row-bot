import type { LearningJob } from '../../api/types';
import { dayWords, tokenWords } from './words';

const JOB_NAMES: Record<LearningJob['job'], string> = {
  drafting: 'Drafting changes',
  practice: 'Making practice tasks',
};

/** What one item of each job's test is, counted ("21 of 24 usable drafts"). */
const PASSED_WORDS: Record<LearningJob['job'], string> = {
  drafting: 'usable drafts',
  practice: 'practice tasks kept',
};

const BACK_WORDS: Record<
  Exclude<NonNullable<LearningJob['reason']>, ''>,
  string
> = {
  not_matched: 'its last test wasn’t close enough',
  new_version: 'a new version is tested first',
  not_served: 'the personal model wasn’t running',
};

function day(at: string | null | undefined): string {
  return at ? dayWords(at.slice(0, 10)) : '';
}

function minutes(seconds: number): string {
  const value = Math.round(seconds / 60);
  return value < 1 ? 'under a minute' : `${value.toLocaleString()} min`;
}

function testWords(job: LearningJob): string {
  const last = job.last;
  if (!last)
    return 'Not tested yet: it’s tested once a personal model version is ready.';
  if (last.verdict === 'too_few')
    return 'Not tested yet: the test needs 20 pieces of your feedback from the last 30 days.';
  if (last.verdict === 'limit')
    return 'The last test stopped at this month’s limit; it runs again later.';
  if (last.verdict === 'unavailable')
    return 'The last test couldn’t run; it runs again later.';
  const result = `${last.personal_passed.toLocaleString()} of ${last.asked.toLocaleString()} ${PASSED_WORDS[job.job]} for v${last.version}, ${last.main_passed.toLocaleString()} for ${job.main_model}`;
  return last.verdict === 'qualified'
    ? `Passed its test (${day(last.at)}): ${result}.`
    : `Not yet (${day(last.at)}): ${result}. It needs to come within 5 points of ${job.main_model}.`;
}

/**
 * How Row-Bot learns › Jobs your personal model does (designs §6, L4c): who does each of Learning's own jobs,
 * since when, its latest test with its denominators, and this month's use of the graphics card.
 */
export function LearningJobs({ jobs }: { jobs: LearningJob[] | undefined }) {
  if (!jobs?.length) return null;
  return (
    <section aria-label="Jobs your personal model does">
      <h3>Jobs your personal model does</h3>
      <ul className="learning-jobs">
        {jobs.map((job) => (
          <li key={job.job}>
            <span className="learning-job-name">{JOB_NAMES[job.job]}</span>
            <span className="learning-job-who">
              {job.by === 'personal'
                ? `Personal model v${job.version}${job.since ? ` · since ${day(job.since)}` : ''}`
                : job.main_model}
            </span>
            <span className="learning-muted">
              {job.by === 'main' && job.reason
                ? `Back with ${job.main_model}: ${BACK_WORDS[job.reason]}. `
                : ''}
              {testWords(job)}
              {job.by === 'personal' &&
              ((job.tokens ?? 0) > 0 || (job.seconds ?? 0) > 0)
                ? ` This month: ${tokenWords(job.tokens ?? 0)} tokens, ${minutes(job.seconds ?? 0)} on the graphics card.`
                : ''}
            </span>
          </li>
        ))}
      </ul>
      <p className="learning-muted">
        It takes over a job only after passing that job’s own test against{' '}
        {jobs[0].main_model}, and it never judges its own next version.
      </p>
    </section>
  );
}

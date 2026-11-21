import { Navigate, useSearchParams } from 'react-router-dom';
import { PHONE_LAYOUT, useMedia } from '../../ui/use-media';
import { useLearningPreview } from '../learning/learning-state';
import SettingsShell from './SettingsShell';

/**
 * Settings opens on Models on a wide layout, beside its list. A phone shows
 * the grouped list first (D6), and each page then has its way back to it;
 * `?view=more` is its second list, More settings (C3).
 */
export default function SettingsIndex() {
  const phone = useMedia(PHONE_LAYOUT);
  const learning = useLearningPreview();
  const [search] = useSearchParams();
  return phone ? (
    <SettingsShell learning={learning} more={search.get('view') === 'more'} />
  ) : (
    <Navigate to="/settings/models" replace />
  );
}

import { Navigate } from 'react-router-dom';
import { PHONE_LAYOUT, useMedia } from '../../ui/use-media';
import SettingsShell from './SettingsShell';

/**
 * Settings opens on Models on a wide layout, beside its list. A phone shows
 * the grouped list first (D6), and each page then has its way back to it.
 */
export default function SettingsIndex() {
  const phone = useMedia(PHONE_LAYOUT);
  return phone ? <SettingsShell /> : <Navigate to="/settings/models" replace />;
}

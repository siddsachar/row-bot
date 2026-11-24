import { useEffect, useState, type ComponentProps } from 'react';
import { Link } from 'react-router-dom';
import { Unplug } from 'lucide-react';
import type { ClientController } from '../../api/controller';
import type { CompanyView } from '../../api/types';
import ModelCatalog from '../settings/ModelCatalog';
import { SettingsGroup, SettingsItem } from '../settings/anatomy';
import { COMPANY_PROVIDER } from './words';

type CatalogProps = Pick<
  ComponentProps<typeof ModelCatalog>,
  'defaults' | 'onDefault' | 'onChanged' | 'refreshSignal'
>;

/**
 * Settings › Models: the company's models as their own group, named after
 * the company (Row-Bot for Teams, C2). Reading the company's state contacts
 * nothing; with no company joined the group isn't there. Disconnected, the
 * models stay listed, unavailable, with the way back.
 */
export default function CompanyModels({
  controller,
  ...catalog
}: { controller: ClientController } & CatalogProps) {
  const [view, setView] = useState<CompanyView | null>(null);
  const { refreshSignal } = catalog;

  useEffect(() => {
    let current = true;
    const read = async () => {
      try {
        const next = await controller.company('status');
        if (current) setView(next);
      } catch {
        // No company state here (a server without it): no group.
      }
    };
    void read();
    return () => {
      current = false;
    };
  }, [controller, refreshSignal]);

  if (!view || !['joined', 'unreachable', 'disconnected'].includes(view.state))
    return null;
  const company = view.company ?? 'Your company';
  const disconnected = view.state === 'disconnected';
  return (
    <SettingsGroup
      title={company}
      anchor="company-models"
      note={`Models ${company} offers you, through its server.`}
    >
      {disconnected && (
        <SettingsItem
          label="Your company disconnected this Row-Bot"
          help="Its models can’t be used until you sign in again."
          icon={<Unplug size={16} aria-hidden />}
          tone="neutral"
          bind={false}
          control={
            <Link className="button small" to="/settings/company">
              Sign in again
            </Link>
          }
        />
      )}
      <ModelCatalog
        controller={controller}
        provider={COMPANY_PROVIDER}
        {...catalog}
      />
    </SettingsGroup>
  );
}

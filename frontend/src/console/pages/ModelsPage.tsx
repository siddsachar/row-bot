import { useSearchParams } from 'react-router-dom';
import { Tabs } from '../../ui/primitives';
import { Page } from '../page';
import { Budgets } from './models/Budgets';
import { Catalog } from './models/Catalog';
import { Endpoints } from './models/Endpoints';
import { Usage } from './models/Usage';

const TABS = [
  { id: 'endpoints', label: 'Endpoints', content: <Endpoints /> },
  { id: 'catalog', label: 'Catalog', content: <Catalog /> },
  { id: 'budgets', label: 'Budgets', content: <Budgets /> },
  { id: 'usage', label: 'Usage', content: <Usage /> },
];

export default function ModelsPage() {
  const [params, setParams] = useSearchParams();
  const tab = TABS.some((item) => item.id === params.get('tab'))
    ? (params.get('tab') as string)
    : 'endpoints';
  return (
    <Page
      title="Models"
      description="The model servers your company runs or pays for, the models people may choose, what they may spend and what they used."
    >
      <Tabs
        label="Models"
        value={tab}
        onChange={(value) => setParams({ tab: value }, { replace: true })}
        items={TABS}
      />
    </Page>
  );
}

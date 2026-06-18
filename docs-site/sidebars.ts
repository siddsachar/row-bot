import type {SidebarsConfig} from '@docusaurus/plugin-content-docs';

const sidebars: SidebarsConfig = {
  docs: [
    'index',
    {
      type: 'category',
      label: 'Getting Started',
      collapsed: false,
      items: [
        'getting-started/index',
        'getting-started/installation',
        'getting-started/first-launch',
      ],
    },
    {
      type: 'category',
      label: 'UI Tour',
      collapsed: false,
      items: ['ui-tour/index'],
    },
    {
      type: 'category',
      label: 'Configuration',
      collapsed: false,
      items: ['configuration/models-and-providers'],
    },
    {
      type: 'category',
      label: 'Guides',
      collapsed: false,
      items: [
        'guides/workflows',
        'guides/designer-studio',
        'guides/developer-studio',
        'guides/skills-plugins-mcp',
        'guides/channels-and-voice',
      ],
    },
    {
      type: 'category',
      label: 'Reference',
      collapsed: false,
      items: ['reference/index', 'reference/generated/index'],
    },
    'privacy-safety/index',
    'troubleshooting/index',
  ],
};

export default sidebars;

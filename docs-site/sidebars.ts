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
      items: [
        'reference/index',
        {
          type: 'category',
          label: 'Generated',
          collapsed: false,
          items: [
            'reference/generated/index',
            'reference/generated/tools',
            'reference/generated/providers',
            'reference/generated/settings',
            'reference/generated/channels',
            'reference/generated/skills',
            'reference/generated/mcp',
            'reference/generated/plugins',
            'reference/generated/data-storage',
            'reference/generated/safety-approvals',
            'reference/generated/environment-and-config',
            'reference/generated/screenshots',
          ],
        },
      ],
    },
    'privacy-safety/index',
    'troubleshooting/index',
  ],
};

export default sidebars;

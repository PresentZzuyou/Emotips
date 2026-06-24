import { mkdir, readFile, writeFile } from 'node:fs/promises';

const token = process.env.NOTION_TOKEN;
const databaseId = process.env.NOTION_TIPS_DATABASE_ID || process.env.NOTION_DATABASE_ID;
const defaultPageId = '389f856893708000b987fbeebd80d5d8';
const pageId = process.env.NOTION_TIPS_PAGE_ID || process.env.NOTION_PAGE_ID || defaultPageId;
const outputPath = new URL('../assets/tips.json', import.meta.url);
const notionVersion = '2022-06-28';

async function ensureFallbackFile() {
  try {
    await readFile(outputPath, 'utf8');
  } catch {
    await writeTipsFile({
      updatedAt: null,
      source: 'notion',
      tips: []
    });
  }
}

async function writeTipsFile(data) {
  await mkdir(new URL('../assets/', import.meta.url), { recursive: true });
  await writeFile(outputPath, `${JSON.stringify(data, null, 2)}\n`, 'utf8');
}

async function notionFetch(path, options = {}) {
  const response = await fetch(`https://api.notion.com/v1${path}`, {
    ...options,
    headers: {
      Authorization: `Bearer ${token}`,
      'Notion-Version': notionVersion,
      'Content-Type': 'application/json',
      ...options.headers
    }
  });

  if (!response.ok) {
    const body = await response.text();
    throw new Error(`Notion API ${response.status}: ${body}`);
  }

  return response.json();
}

function richTextToPlain(richText = []) {
  return richText.map((item) => item.plain_text || '').join('').trim();
}

function normalizeNotionId(id) {
  const cleanId = String(id || '').replace(/-/g, '');
  if (!/^[a-f0-9]{32}$/i.test(cleanId)) return id;
  return [
    cleanId.slice(0, 8),
    cleanId.slice(8, 12),
    cleanId.slice(12, 16),
    cleanId.slice(16, 20),
    cleanId.slice(20)
  ].join('-');
}

function findTitle(page) {
  const titleProperty = Object.values(page.properties || {}).find((property) => property.type === 'title');
  return richTextToPlain(titleProperty?.title) || '제목 없는 질문';
}

function findRichTextContent(page) {
  const preferredNames = new Set(['content', 'body', 'text', 'description', 'summary', '내용', '본문', '설명']);
  const entries = Object.entries(page.properties || {});

  const preferred = entries.find(([name, property]) => {
    return property.type === 'rich_text' && preferredNames.has(name.toLowerCase());
  });

  if (preferred) return richTextToPlain(preferred[1].rich_text);

  const firstRichText = entries.find(([, property]) => property.type === 'rich_text');
  return firstRichText ? richTextToPlain(firstRichText[1].rich_text) : '';
}

function blockToText(block, index) {
  const type = block.type;
  const value = block[type];
  const text = richTextToPlain(value?.rich_text);
  if (!text) return '';

  if (type === 'heading_1' || type === 'heading_2' || type === 'heading_3') return text;
  if (type === 'bulleted_list_item') return `- ${text}`;
  if (type === 'numbered_list_item') return `${index + 1}. ${text}`;
  if (type === 'quote') return `> ${text}`;
  if (type === 'to_do') return `${value.checked ? '[x]' : '[ ]'} ${text}`;
  return text;
}

async function fetchBlockContent(pageId) {
  const blocks = [];
  let cursor;

  do {
    const query = new URLSearchParams({ page_size: '100' });
    if (cursor) query.set('start_cursor', cursor);

    const data = await notionFetch(`/blocks/${pageId}/children?${query.toString()}`, {
      method: 'GET'
    });

    blocks.push(...data.results);
    cursor = data.has_more ? data.next_cursor : undefined;
  } while (cursor);

  return blocks
    .map((block, index) => blockToText(block, index))
    .filter(Boolean)
    .join('\n\n')
    .trim();
}

async function fetchDatabasePages() {
  const pages = [];
  let cursor;

  do {
    const body = {
      page_size: 100,
      sorts: [{ timestamp: 'last_edited_time', direction: 'descending' }]
    };
    if (cursor) body.start_cursor = cursor;

    const data = await notionFetch(`/databases/${databaseId}/query`, {
      method: 'POST',
      body: JSON.stringify(body)
    });

    pages.push(...data.results.filter((page) => !page.archived));
    cursor = data.has_more ? data.next_cursor : undefined;
  } while (cursor);

  return pages;
}

async function fetchPageChildren(rootPageId) {
  const blocks = [];
  let cursor;
  const normalizedPageId = normalizeNotionId(rootPageId);

  do {
    const query = new URLSearchParams({ page_size: '100' });
    if (cursor) query.set('start_cursor', cursor);

    const data = await notionFetch(`/blocks/${normalizedPageId}/children?${query.toString()}`, {
      method: 'GET'
    });

    blocks.push(...data.results);
    cursor = data.has_more ? data.next_cursor : undefined;
  } while (cursor);

  return blocks;
}

async function fetchPage(pageId) {
  return notionFetch(`/pages/${normalizeNotionId(pageId)}`, {
    method: 'GET'
  });
}

async function fetchPageTips(rootPageId) {
  const rootBlocks = await fetchPageChildren(rootPageId);
  const childPages = rootBlocks.filter((block) => block.type === 'child_page');

  if (!childPages.length) {
    const rootPage = await fetchPage(rootPageId);
    return [{
      id: rootPage.id,
      title: findTitle(rootPage),
      content: rootBlocks.map((block, index) => blockToText(block, index)).filter(Boolean).join('\n\n').trim(),
      url: rootPage.url || '',
      lastEditedTime: rootPage.last_edited_time || ''
    }];
  }

  const tips = [];
  for (const block of childPages) {
    const page = await fetchPage(block.id);
    tips.push({
      id: page.id,
      title: findTitle(page) || block.child_page?.title || '제목 없는 질문',
      content: await fetchBlockContent(page.id),
      url: page.url || '',
      lastEditedTime: page.last_edited_time || block.last_edited_time || ''
    });
  }

  return tips.sort((a, b) => String(b.lastEditedTime).localeCompare(String(a.lastEditedTime)));
}

async function main() {
  if (!token) {
    console.log('Notion secrets are missing. Keeping existing assets/tips.json.');
    await ensureFallbackFile();
    return;
  }

  const pages = databaseId ? await fetchDatabasePages() : [];
  const tips = [];

  for (const page of pages) {
    const propertyContent = findRichTextContent(page);
    const blockContent = propertyContent || await fetchBlockContent(page.id);

    tips.push({
      id: page.id,
      title: findTitle(page),
      content: blockContent,
      url: page.url || '',
      lastEditedTime: page.last_edited_time || ''
    });
  }

  if (!databaseId && pageId) {
    tips.push(...await fetchPageTips(pageId));
  }

  await writeTipsFile({
    updatedAt: new Date().toISOString(),
    source: 'notion',
    tips
  });

  console.log(`Synced ${tips.length} Notion tips.`);
}

main().catch((error) => {
  console.error(error.message);
  process.exit(1);
});

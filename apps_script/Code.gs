const APP_SECRET = 'REPLACE_WITH_LONG_RANDOM_SECRET';

function doPost(e) {
  try {
    const body = parseRequestBody_(e);
    assertSecret_(body.secret);

    const action = String(body.action || '');
    if (!action) {
      return jsonResponse_({ ok: false, error: 'Action is required.' });
    }

    if (action === 'getPendingTasks') {
      return jsonResponse_({
        ok: true,
        tasks: getPendingTasks_(body.spreadsheetUrl, body.sourceSheetName, body.statusValue, body.statusValues || []),
      });
    }

    if (action === 'getDocumentStructure') {
      return jsonResponse_({
        ok: true,
        document: getDocumentStructure_(body.documentUrl, body.excludedTitles || []),
      });
    }

    if (action === 'appendComment') {
      appendComment_(
        body.commentsSpreadsheetUrl || body.spreadsheetUrl,
        body.commentsSheetName,
        body.comment || {}
      );
      return jsonResponse_({ ok: true });
    }

    if (action === 'updateArticleStatus') {
      updateArticleStatus_(
        body.spreadsheetUrl,
        body.sourceSheetName,
        body.rowNumber,
        body.newStatus
      );
      return jsonResponse_({ ok: true });
    }

    return jsonResponse_({ ok: false, error: 'Unknown action: ' + action });
  } catch (error) {
    return jsonResponse_({
      ok: false,
      error: String(error && error.message ? error.message : error),
    });
  }
}

function parseRequestBody_(e) {
  if (!e || !e.postData || !e.postData.contents) {
    throw new Error('Empty request body.');
  }
  return JSON.parse(e.postData.contents);
}

function assertSecret_(secret) {
  if (!secret || String(secret) !== APP_SECRET) {
    throw new Error('Invalid secret.');
  }
}

function jsonResponse_(payload) {
  return ContentService
    .createTextOutput(JSON.stringify(payload))
    .setMimeType(ContentService.MimeType.JSON);
}

function getPendingTasks_(spreadsheetUrl, sheetName, statusValue) {
  const spreadsheet = SpreadsheetApp.openByUrl(String(spreadsheetUrl));
  const sheet = spreadsheet.getSheetByName(String(sheetName));
  if (!sheet) {
    throw new Error('Sheet not found: ' + sheetName);
  }

  const lastRow = sheet.getLastRow();
  if (lastRow < 2) {
    return [];
  }

  const values = sheet.getRange(2, 1, lastRow - 1, 14).getDisplayValues();
  const tasks = [];
  const statusValues = Array.isArray(arguments[3]) && arguments[3].length
    ? arguments[3].map((item) => String(item || '').trim())
    : [String(statusValue || '').trim()];

  for (let index = 0; index < values.length; index += 1) {
    const row = values[index];
    if (statusValues.indexOf((row[3] || '').trim()) === -1) {
      continue;
    }
    if (!(row[7] || '').trim() || !(row[9] || '').trim()) {
      continue;
    }

    tasks.push({
      rowNumber: index + 2,
      articleId: row[0] || '',
      direction: row[1] || '',
      topic: row[2] || '',
      status: row[3] || '',
      author: row[4] || '',
      dueDate: row[5] || '',
      documentUrl: row[7] || '',
      siteUrl: row[8] || '',
      doctorName: row[9] || '',
      priority: row[13] || '',
    });
  }

  return tasks;
}

function getDocumentStructure_(documentUrl, excludedTitles) {
  const docId = extractGoogleId_(String(documentUrl));
  const doc = DocumentApp.openById(docId);
  const items = [];
  readContainer_(doc.getBody(), items);

  const excludedMap = {};
  for (let i = 0; i < excludedTitles.length; i += 1) {
    excludedMap[normalizeText_(excludedTitles[i])] = true;
  }

  let title = String(doc.getName() || '').trim();
  const introParts = [];
  const introIllustrations = [];
  const sections = [];
  let currentTitle = null;
  let currentBody = [];
  let currentIllustrations = [];

  function flushSection() {
    if (!currentTitle) {
      return;
    }
    if (excludedMap[normalizeText_(currentTitle)]) {
      currentTitle = null;
      currentBody = [];
      currentIllustrations = [];
      return;
    }
    sections.push({
      index: sections.length + 1,
      title: currentTitle,
      body: currentBody.filter(Boolean).join('\n\n').trim(),
      illustrations: currentIllustrations,
    });
    currentTitle = null;
    currentBody = [];
    currentIllustrations = [];
  }

  for (let i = 0; i < items.length; i += 1) {
    const item = items[i];
    if (item.type === 'IMAGE') {
      if (!currentTitle) {
        introIllustrations.push(item.image);
      } else {
        currentIllustrations.push(item.image);
      }
      continue;
    }

    if (item.style === 'HEADING_1') {
      if (!title) {
        title = item.text;
      }
      continue;
    }

    if (item.style === 'HEADING_2') {
      flushSection();
      currentTitle = item.text;
      currentBody = [];
      currentIllustrations = [];
      continue;
    }

    if (!currentTitle) {
      introParts.push(item.text);
    } else {
      currentBody.push(item.text);
    }
  }

  flushSection();

  if (!sections.length) {
    sections.push({
      index: 1,
      title: 'Текст статьи',
      body: introParts.filter(Boolean).join('\n\n').trim(),
      illustrations: introIllustrations,
    });
    introParts.length = 0;
    introIllustrations.length = 0;
  }

  return {
    docId: docId,
    title: title || 'Без названия',
    intro: introParts.filter(Boolean).join('\n\n').trim(),
    introIllustrations: introIllustrations,
    sections: sections,
  };
}

function readContainer_(container, items) {
  const count = container.getNumChildren();
  for (let index = 0; index < count; index += 1) {
    const child = container.getChild(index);
    const type = child.getType();

    if (type === DocumentApp.ElementType.PARAGRAPH) {
      readParagraph_(child.asParagraph(), items);
      continue;
    }

    if (type === DocumentApp.ElementType.LIST_ITEM) {
      readListItem_(child.asListItem(), items);
      continue;
    }

    if (type === DocumentApp.ElementType.TABLE) {
      readTable_(child.asTable(), items);
    }
  }
}

function readParagraph_(paragraph, items) {
  const text = getStyledText_(paragraph).trim();
  if (!text) {
  } else {
    items.push({
      style: mapHeading_(paragraph.getHeading()),
      text: text,
    });
  }

  pushInlineImages_(paragraph, items);
  pushPositionedImages_(paragraph, items);
}

function readListItem_(listItem, items) {
  const text = getStyledText_(listItem).trim();
  if (!text) {
  } else {
    items.push({
      style: mapHeading_(listItem.getHeading()),
      text: '• ' + text,
    });
  }

  pushInlineImages_(listItem, items);
  pushPositionedImages_(listItem, items);
}

function readTable_(table, items) {
  for (let rowIndex = 0; rowIndex < table.getNumRows(); rowIndex += 1) {
    const row = table.getRow(rowIndex);
    for (let cellIndex = 0; cellIndex < row.getNumCells(); cellIndex += 1) {
      readContainer_(row.getCell(cellIndex), items);
    }
  }
}

function mapHeading_(heading) {
  if (heading === DocumentApp.ParagraphHeading.HEADING1) {
    return 'HEADING_1';
  }
  if (heading === DocumentApp.ParagraphHeading.HEADING2) {
    return 'HEADING_2';
  }
  return 'NORMAL_TEXT';
}

function getStyledText_(container) {
  const textElement = container.editAsText();
  const fullText = String(textElement.getText() || '').replace(/\u000b/g, ' ');
  if (!fullText) {
    return '';
  }

  const indexes = textElement.getTextAttributeIndices();
  const parts = [];

  for (let i = 0; i < indexes.length; i += 1) {
    const start = indexes[i];
    const end = i + 1 < indexes.length ? indexes[i + 1] : fullText.length;
    const piece = fullText.slice(start, end);
    if (!piece) {
      continue;
    }

    const isBold = Boolean(textElement.isBold(start));
    if (
      parts.length > 0 &&
      parts[parts.length - 1].bold &&
      !isBold &&
      !/^[\s\n]/.test(piece) &&
      !/[\s\n]$/.test(parts[parts.length - 1].text)
    ) {
      parts.push({ text: '\n', bold: false });
    }

    parts.push({ text: piece, bold: isBold });
  }

  return parts.map((part) => part.text).join('');
}

function pushInlineImages_(container, items) {
  const childCount = container.getNumChildren();
  for (let index = 0; index < childCount; index += 1) {
    const child = container.getChild(index);
    if (child.getType() !== DocumentApp.ElementType.INLINE_IMAGE) {
      continue;
    }
    const payload = buildImagePayload_(child.asInlineImage(), index + 1);
    if (payload) {
      items.push({
        type: 'IMAGE',
        image: payload,
      });
    }
  }
}

function pushPositionedImages_(container, items) {
  if (typeof container.getPositionedImages !== 'function') {
    return;
  }
  const images = container.getPositionedImages();
  for (let index = 0; index < images.length; index += 1) {
    const payload = buildImagePayload_(images[index], index + 1);
    if (payload) {
      items.push({
        type: 'IMAGE',
        image: payload,
      });
    }
  }
}

function buildImagePayload_(image, index) {
  const blob = image.getBlob();
  const mimeType = String(blob.getContentType() || 'image/jpeg');
  const altTitle = typeof image.getAltTitle === 'function' ? String(image.getAltTitle() || '') : '';
  const altDescription = typeof image.getAltDescription === 'function' ? String(image.getAltDescription() || '') : '';
  const fileBase = sanitizeFileName_(altTitle || altDescription || ('illustration-' + index));
  return {
    mimeType: mimeType,
    filename: fileBase + extensionForMimeType_(mimeType),
    altTitle: altTitle,
    altDescription: altDescription,
    contentBase64: Utilities.base64Encode(blob.getBytes()),
  };
}

function extensionForMimeType_(mimeType) {
  if (mimeType === 'image/png') {
    return '.png';
  }
  if (mimeType === 'image/webp') {
    return '.webp';
  }
  if (mimeType === 'image/gif') {
    return '.gif';
  }
  return '.jpg';
}

function sanitizeFileName_(value) {
  const normalized = String(value || 'illustration')
    .replace(/[^\wа-яА-ЯёЁ.-]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 60);
  return normalized || 'illustration';
}

function appendComment_(spreadsheetUrl, commentsSheetName, comment) {
  const spreadsheet = SpreadsheetApp.openByUrl(String(spreadsheetUrl));
  const targetSheetName = String(commentsSheetName || 'Комментарии врачей');
  let sheet = spreadsheet.getSheetByName(targetSheetName);
  const headers = [[
    'Создано',
    'Врач',
    'Статья',
    'Раздел',
    'Цитата',
    'Комментарий',
    'Документ',
    'Строка таблицы',
    'ID статьи',
    'Messenger user id',
  ]];

  if (!sheet) {
    sheet = spreadsheet.insertSheet(targetSheetName);
  }

  if (sheet.getLastRow() === 0) {
    sheet.getRange(1, 1, 1, headers[0].length).setValues(headers);
  } else {
    const firstCell = String(sheet.getRange(1, 1).getDisplayValue() || '').trim();
    if (firstCell === 'Создано') {
      sheet.getRange(1, 1, 1, headers[0].length).setValues(headers);
    }
  }

  sheet.appendRow([
    comment.createdAt || '',
    comment.doctorName || '',
    comment.articleTitle || '',
    comment.sectionTitle || '',
    comment.quoteText || '',
    comment.commentText || '',
    comment.documentUrl || '',
    comment.sheetRowNumber || '',
    comment.articleId || '',
    comment.telegramUserId || '',
  ]);
}

function updateArticleStatus_(spreadsheetUrl, sheetName, rowNumber, newStatus) {
  const spreadsheet = SpreadsheetApp.openByUrl(String(spreadsheetUrl));
  const sheet = spreadsheet.getSheetByName(String(sheetName));
  if (!sheet) {
    throw new Error('Sheet not found: ' + sheetName);
  }
  sheet.getRange(Number(rowNumber), 4).setValue(String(newStatus || ''));
}

function normalizeText_(value) {
  return String(value || '')
    .toLowerCase()
    .replace(/ё/g, 'е')
    .replace(/\s+/g, ' ')
    .trim();
}

function extractGoogleId_(url) {
  const match = String(url).match(/\/d\/([a-zA-Z0-9-_]+)/);
  if (!match) {
    throw new Error('Invalid Google file URL: ' + url);
  }
  return match[1];
}

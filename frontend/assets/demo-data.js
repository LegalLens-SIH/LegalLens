(function (global) {
  'use strict';

  var officialScans = [
    {
      id: 'DEMO-OF-001',
      productName: 'Demo Premium Chips',
      category: 'Food & Beverage',
      manufacturer: 'Demo Foods Pvt. Ltd.',
      date: '2026-09-18T10:15:00',
      status: 'flagged',
      score: 82,
      caseId: 'DEMO-CASE-001',
      findings: [
        { field: 'Product Name', value: 'Demo Premium Chips', status: 'compliant' },
        { field: 'Net Quantity', value: '50 g', status: 'compliant' },
        { field: 'MRP (Incl. of all taxes)', value: '₹20', status: 'compliant' },
        { field: 'Manufacturing Date', value: 'May 2026', status: 'low_confidence' },
        { field: 'Consumer Care Details', value: 'support@demofoods.demo', status: 'compliant' }
      ]
    },
    {
      id: 'DEMO-OF-002',
      productName: 'Demo Protein Bar',
      category: 'Food & Beverage',
      manufacturer: 'Demo Foods Pvt. Ltd.',
      date: '2026-09-17T11:40:00',
      status: 'compliant',
      score: 94,
      caseId: null,
      findings: [
        { field: 'Product Name', value: 'Demo Protein Bar', status: 'compliant' },
        { field: 'Net Quantity', value: '45 g', status: 'compliant' },
        { field: 'MRP (Incl. of all taxes)', value: '₹75', status: 'compliant' },
        { field: 'Mfg Date', value: '06/2026', status: 'compliant' },
        { field: 'Consumer Care Details', value: 'care@demofoods.demo', status: 'compliant' }
      ]
    },
    {
      id: 'DEMO-OF-003',
      productName: 'Demo Citrus Soda',
      category: 'Beverages',
      manufacturer: 'Demo Beverages Ltd.',
      date: '2026-09-15T09:10:00',
      status: 'pending',
      score: 63,
      caseId: 'DEMO-CASE-002',
      findings: [
        { field: 'Product Name', value: 'Demo Citrus Soda', status: 'compliant' },
        { field: 'Net Quantity', value: '500 ml', status: 'compliant' },
        { field: 'Mrp', value: '₹40', status: 'low_confidence' },
        { field: 'Manufacturing Date', value: 'Not clearly legible', status: 'missing' },
        { field: 'Consumer Care Details', value: 'support@demobeverages.demo', status: 'compliant' }
      ]
    },
    {
      id: 'DEMO-OF-004',
      productName: 'Demo Herbal Sachets',
      category: 'Pharmaceuticals',
      manufacturer: 'Demo Wellness Pvt. Ltd.',
      date: '2026-09-13T14:20:00',
      status: 'compliant',
      score: 100,
      caseId: null,
      findings: [
        { field: 'Product Name', value: 'Demo Herbal Sachets', status: 'compliant' },
        { field: 'Net Quantity', value: '10 packets', status: 'compliant' },
        { field: 'MRP (Incl. of all taxes)', value: '₹120', status: 'compliant' },
        { field: 'Batch No.', value: 'DEMO-HT-118', status: 'compliant' },
        { field: 'Consumer Care Details', value: 'care@demowellness.demo', status: 'compliant' }
      ]
    }
  ];

  var officialCases = [
    {
      id: 'DEMO-CASE-001',
      scanId: 'DEMO-OF-001',
      entity: 'Demo Foods Pvt. Ltd.',
      violationType: 'Section 14(a) - Manufacturing date partially illegible',
      confidence: 91,
      status: 'under_review',
      timeline: [
        { actor: 'System', title: 'Scan uploaded', desc: 'Demo image batch ingested for test demonstration.', date: '2026-09-18T10:15:00' },
        { actor: 'Demo Enforcement Official', title: 'Violation logged', desc: 'Manufacturing date requires manual verification.', date: '2026-09-18T10:26:00' }
      ]
    },
    {
      id: 'DEMO-CASE-002',
      scanId: 'DEMO-OF-003',
      entity: 'Demo Beverages Ltd.',
      violationType: 'Section 14(a) - MRP formatting and date legibility',
      confidence: 86,
      status: 'open',
      timeline: [
        { actor: 'System', title: 'Scan uploaded', desc: 'Image data captured from demo product sample.', date: '2026-09-15T09:10:00' },
        { actor: 'Demo Enforcement Official', title: 'Flag created', desc: 'Price and date fields need clarifying review.', date: '2026-09-15T09:25:00' }
      ]
    }
  ];

  var manufacturerProducts = [
    { id: 'DEMO-PROD-101', productName: 'Demo Premium Chips', sku: 'DF-CHIP-001', category: 'Food & Beverage', latestStatus: 'needs_fixes', latestScore: 82 },
    { id: 'DEMO-PROD-102', productName: 'Demo Protein Bar', sku: 'DF-PRO-045', category: 'Food & Beverage', latestStatus: 'compliant', latestScore: 94 },
    { id: 'DEMO-PROD-103', productName: 'Demo Citrus Soda', sku: 'DB-SODA-230', category: 'Beverages', latestStatus: 'needs_fixes', latestScore: 63 },
    { id: 'DEMO-PROD-104', productName: 'Demo Herbal Sachets', sku: 'DW-HERB-118', category: 'Healthcare', latestStatus: 'compliant', latestScore: 100 }
  ];

  var manufacturerChecks = [
    {
      id: 'DEMO-SC-101',
      productId: 'DEMO-PROD-101',
      productName: 'Demo Premium Chips',
      category: 'Food & Beverage',
      version: 1,
      date: '2026-09-18T10:15:00',
      score: 82,
      status: 'needs_fixes',
      findings: [
        { field: 'Net Quantity', status: 'compliant', note: 'Declaration is legible and correctly formatted.' },
        { field: 'MRP', status: 'warning', note: 'MRP has minor formatting issue. Please verify inclusive tax wording.' },
        { field: 'Manufacturing Date', status: 'warning', note: 'Date is partially illegible under inspection lighting.' }
      ]
    },
    {
      id: 'DEMO-SC-102',
      productId: 'DEMO-PROD-102',
      productName: 'Demo Protein Bar',
      category: 'Food & Beverage',
      version: 1,
      date: '2026-09-17T11:40:00',
      score: 94,
      status: 'compliant',
      findings: [
        { field: 'Net Quantity', status: 'compliant', note: 'Fully compliant and easy to read.' },
        { field: 'MRP', status: 'compliant', note: 'Accurate and legible.' },
        { field: 'Manufacturing Date', status: 'compliant', note: 'Complete and readable.' }
      ]
    },
    {
      id: 'DEMO-SC-103',
      productId: 'DEMO-PROD-103',
      productName: 'Demo Citrus Soda',
      category: 'Beverages',
      version: 1,
      date: '2026-09-15T09:10:00',
      score: 63,
      status: 'needs_fixes',
      findings: [
        { field: 'MRP', status: 'warning', note: 'Price and tax wording need clearer placement.' },
        { field: 'Manufacturing Date', status: 'warning', note: 'Date should be made more legible.' },
        { field: 'Consumer Care Details', status: 'compliant', note: 'Available and visible.' }
      ]
    },
    {
      id: 'DEMO-SC-104',
      productId: 'DEMO-PROD-104',
      productName: 'Demo Herbal Sachets',
      category: 'Healthcare',
      version: 1,
      date: '2026-09-12T12:30:00',
      score: 100,
      status: 'compliant',
      findings: [
        { field: 'Net Quantity', status: 'compliant', note: 'Declaration matches packaging design and legibility rules.' },
        { field: 'MRP', status: 'compliant', note: 'Fully compliant with inclusive-of-all-taxes labeling.' },
        { field: 'Batch No.', status: 'compliant', note: 'Legible and complete.' }
      ]
    }
  ];

  global.LLDemoData = {
    official: {
      stats: {
        totalScans: 24,
        compliant: 15,
        actionRequired: 6,
        nonCompliant: 3,
        openViolations: 6,
        complianceRate: 82
      },
      scans: officialScans,
      cases: officialCases
    },
    manufacturer: {
      stats: {
        totalProducts: 12,
        compliantProducts: 8,
        needsFixes: 3,
        nonCompliant: 1,
        complianceScore: 82
      },
      products: manufacturerProducts,
      selfChecks: manufacturerChecks
    }
  };
})(window);
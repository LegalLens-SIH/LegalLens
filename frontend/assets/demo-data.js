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
    }
  };
})(window);
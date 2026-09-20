| id | dimension | prompt | scored on | verified against |
|---|---|---|---|---|
| geo-correct-001 | correctness | What organism and assay type is GSE176078, and how many samples does it have? | exact organism; numeric n_samples; contains assay | NCBI esummary db=gds uid 200176078; GEO SOFT GSE176078 |
| geo-correct-002 | correctness | Which platform (GPL accession) was used for GSE120575, and which PubMed ID is it linked to? | exact platform; exact pubmed | NCBI esummary db=gds uid 200120575 |
| geo-correct-003 | correctness | Is GSE239940 a SuperSeries? If so, list its SubSeries accessions and how many samples each has. | exact is_superseries; exact sub_a; exact sub_b; numeric n_sub_b | GEO SOFT GSE239940 (!Series_relation), GSE239938, GSE239939 |
| geo-correct-004 | correctness | What clinical subtype and tissue are recorded for GEO sample GSM5354513? | contains subtype; contains tissue | GEO SOFT GSM5354513 (!Sample_characteristics_ch1) |
| geo-route-001 | routing | Tell me about GSE176078. | first call is role `series_lookup` | no ground truth needed; scored on first tool call |
| geo-route-002 | routing | I need single-cell RNA-seq datasets of human pancreatic organoids. | first call is role `series_search` | scored on first tool call |
| geo-route-003 | routing | What condition was sample GSM5354513 in? | first call is role `sample_lookup` | scored on first tool call |
| geo-route-004 | routing | Find recruiting clinical trials for pancreatic cancer immunotherapy. | first call is not the server under test (→ clinicaltrials) | scored on first tool call |
| geo-route-005 | routing | Find recent papers on CRISPR base editing in primary T cells. | first call is not the server under test (→ pubmed) | scored on first tool call |
| geo-budget-001 | budget | Summarize GSE115978 in a few sentences: what it studied, organism, and roughly how many samples. | results ≤ 1500 tok | NCBI esummary db=gds uid 200115978 |
| geo-budget-002 | budget | List the sample accessions in GSE115978. | results ≤ 4000 tok, truncation disclosed | NCBI esummary db=gds uid 200115978 |
| geo-recover-001 | recovery | Tell me about GSE999999999. | error surfaced; any says_not_found; absent no_invented_title | GSE999999999 does not exist (esearch returns 0 ids) |
| geo-recover-002 | recovery | Find GEO series on pancreatic organoid single-cell RNA-seq published in 2031. | error surfaced; contains acknowledges_year | date is in the future by construction |
| ot-correct-001 | correctness | What is the Ensembl gene ID for BRCA1, and which chromosome is it on? | exact ensembl; numeric chromosome | OT GraphQL target(ensemblId) + search(queryString:"BRCA1", entityNames:[target]) |
| ot-correct-002 | correctness | What is gefitinib's ChEMBL ID, what type of drug is it, and what is its mechanism of action target? | exact chembl; contains small_molecule; exact target; any action | OT GraphQL drug(chemblId:"CHEMBL939") mechanismsOfAction |
| ot-correct-003 | correctness | What is the Open Targets disease ID for Alzheimer disease, and which therapeutic areas is it filed under? | exact mondo; contains area_nervous | OT GraphQL search(queryString:"Alzheimer disease", entityNames:[disease]) + disease(efoId) |
| ot-correct-004 | correctness | Which disease is most strongly associated with BRCA1 in Open Targets? | contains breast_cancer | OT GraphQL target(ensemblId:"ENSG00000012048") associatedDiseases(page:{index:0,size:3}) |
| ot-route-001 | routing | Give me the basic details of the gene EGFR. | first call is role `target_lookup` | scored on first tool call |
| ot-route-002 | routing | What drugs are in clinical development or approved for EGFR? | first call is role `target_drugs` | scored on first tool call |
| ot-route-003 | routing | Find clinical trials currently recruiting for EGFR-mutant lung cancer. | first call is not the server under test (→ clinicaltrials) | scored on first tool call |
| ot-route-004 | routing | Find recent review articles about EGFR inhibitor resistance mechanisms. | first call is not the server under test (→ pubmed) | scored on first tool call |
| ot-budget-001 | budget | Summarize what is known about the target EGFR: what it is, and its main disease associations. | results ≤ 3000 tok | OT GraphQL target(ensemblId:"ENSG00000146648") associatedDiseases count = 6459 |
| ot-budget-002 | budget | List all diseases associated with BRCA1. | results ≤ 4000 tok, truncation disclosed | OT GraphQL target(ensemblId:"ENSG00000012048") associatedDiseases count |
| ot-recover-001 | recovery | Tell me about the target ENSG99999999999. | error surfaced; any says_not_found; absent no_invented_symbol | OT GraphQL target(ensemblId:"ENSG99999999999") returns null |
| ot-recover-002 | recovery | Which drugs are known to act on the target EGFR? Use the knownDrugs data. | error surfaced; any names_a_drug | OT GraphQL: knownDrugs -> 'Cannot query field'; drugAndClinicalCandidates exists on Target at 26.06; gefitinib CHEMBL939 targets EGFR |

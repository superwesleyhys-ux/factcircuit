"""Synthetic transport/alias recovery checks, never model performance evidence."""
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parents[1]))
from newsverify.news_sources import SourceDocument
from run_test import validate_prediction, CUTOFF
from retrieval import retrieve_sources


@dataclass(frozen=True)
class DatedDocument(SourceDocument):
    @property
    def available_at(self):
        return "2023-01-01T00:00:00Z"
    @property
    def availability_basis(self):
        return "Explicitly synthetic dated fixture"


SEED="https://fixture.invalid/news"
DOI="https://doi.org/10.5555/example"
PAPER="https://fixture.invalid/paper"


def collector():
    seed=DatedDocument(SEED,"News","Original report: https://doi.org/10.5555/example",CUTOFF,
                       links=[{"url":DOI,"text":"study"}])
    paper=DatedDocument(PAPER,"Paper","A synthetic original measurement record.",CUTOFF)
    return SimpleNamespace(documents={SEED:seed,PAPER:paper},requests=[
        {"operation":"fetch","url":SEED,"final_url":SEED,"success":True},
        {"operation":"fetch","url":DOI,"final_url":PAPER,"success":True}],errors=[],max_documents=6)


def prediction():
    return dict(fact_status="unresolved",origin_url=PAPER,origin_chain=[SEED,DOI,PAPER],
                risk="insufficient_evidence",fabrication_established=False,citations=[],rationale="Synthetic fixture.")


def action(kind,**kwargs):
    return dict(action=kind,urls=[],source_url="",quotes=[],author="",journal="",publication_date="",
                keywords=[],rationale="Synthetic fixture.") | kwargs


class Transport:
    def __init__(self, steps):self.steps=list(steps);self.packets=[]
    def generate(self,stage,instructions,packet,schema):
        self.packets.append(deepcopy(packet));return self.steps.pop(0)


class RecoveryChecks(unittest.TestCase):
    def test_doi_redirect_is_not_a_cycle(self):
        validate_prediction(prediction(),collector(),SEED)

    def test_real_cycle_is_rejected(self):
        p=prediction();p["origin_chain"]=[SEED,DOI,SEED,PAPER]
        with self.assertRaises(ValueError):validate_prediction(p,collector(),SEED)

    def test_unknown_url_is_rejected(self):
        p=prediction();p["origin_chain"]=[SEED,"https://unfetched.invalid/x",PAPER]
        with self.assertRaises(ValueError):validate_prediction(p,collector(),SEED)

    def test_unobserved_edge_is_rejected(self):
        c=collector();c.documents[SEED]=DatedDocument(SEED,"No link","No source is given.",CUTOFF)
        with self.assertRaises(ValueError):validate_prediction(prediction(),c,SEED)

    def test_invalid_bibliography_is_feedback_not_an_accepted_result(self):
        c=collector();t=Transport([action("bibliography",source_url="https://unfetched.invalid/x"),action("stop")])
        with tempfile.TemporaryDirectory() as tmp:
            result=retrieve_sources({"url":SEED,"claim":"Synthetic claim"},c,t,
                                    arm="direct",directory=Path(tmp),cutoff=CUTOFF)
        self.assertEqual([],result["bibliographic_bindings"])
        self.assertEqual(2,len(t.packets))
        self.assertIn("tool_error",t.packets[1]["prior_decisions"][0])

    def test_bad_tool_requests_cannot_expand_decision_budget(self):
        c=collector();t=Transport([action("fetch",urls=["https://unobserved.invalid/x"]) for _ in range(4)])
        with tempfile.TemporaryDirectory() as tmp:
            result=retrieve_sources({"url":SEED,"claim":"Synthetic claim"},c,t,
                                    arm="harness",directory=Path(tmp),cutoff=CUTOFF)
        self.assertEqual(4,len(t.packets));self.assertTrue(all("tool_error" in d for d in result["decisions"]))


if __name__=="__main__":unittest.main()

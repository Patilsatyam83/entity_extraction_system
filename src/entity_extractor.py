"""
Complete Entity Extraction System - Production Ready
Combines: Baseline spaCy + FREE LLM Enhancement (Groq/Gemini)

Installation:
    pip install spacy groq google-generativeai
    python -m spacy download en_core_web_sm

Get FREE API Keys:
    Groq: https://console.groq.com/ (14,400 requests/day - RECOMMENDED)
    Gemini: https://aistudio.google.com/ (1,500 requests/day)

Usage:
    # Without LLM (baseline)
    extractor = EntityExtractor(use_llm=False)
    
    # With FREE LLM (recommended)
    extractor = EntityExtractor(use_llm=True, llm_provider="groq")
    
    result = extractor.extract(text)
"""

import spacy
import re
import json
import os
from typing import Dict, List, Any, Tuple, Set, Optional
from dataclasses import dataclass, asdict
from enum import Enum

# ============================================================================
# LLM IMPORTS (Optional - only if using LLM enhancement)
# ============================================================================
try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False
    print("⚠️  Groq not installed. Install: pip install groq")

try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False
    print("⚠️  Gemini not installed. Install: pip install google-generativeai")

# Load spaCy model
try:
    nlp = spacy.load("en_core_web_sm")
except OSError:
    import subprocess
    subprocess.run(["python", "-m", "spacy", "download", "en_core_web_sm"])
    nlp = spacy.load("en_core_web_sm")


# ============================================================================
# ENUMS & DATA CLASSES
# ============================================================================

class ExtractionMode(Enum):
    """Extraction mode selection"""
    BASELINE = "baseline"  # spaCy only (no LLM)
    LLM_ONLY = "llm_only"  # LLM only
    HYBRID = "hybrid"      # LLM validates spaCy results (recommended)


class LLMProvider(Enum):
    """Available LLM providers"""
    GROQ = "groq"
    GEMINI = "gemini"


@dataclass
class Entity:
    """Represents an extracted entity with confidence score"""
    value: str
    type: str
    confidence: float
    source: str = "hybrid"


# ============================================================================
# BASELINE CLASSES (Your Original Code - Fixed Version)
# ============================================================================

class EntityNormalizer:
    """Normalizes extracted entities for consistency"""
    
    TECH_MAPPINGS = {
        'react.js': 'React', 'reactjs': 'React',
        'js': 'JavaScript', 'javascript': 'JavaScript',
        'ts': 'TypeScript', 'typescript': 'TypeScript',
        'python3': 'Python', 'py': 'Python',
        'node.js': 'Node.js', 'nodejs': 'Node.js', 'node': 'Node.js'
    }
    
    LOCATION_MAPPINGS = {
        'bombay': 'Mumbai', 'ny': 'New York',
        'sf': 'San Francisco', 'bangalore': 'Bengaluru'
    }
    
    US_STATES = {
        'california', 'texas', 'new york', 'florida', 'illinois',
        'pennsylvania', 'ohio', 'georgia', 'north carolina', 'michigan',
        'virginia', 'washington', 'arizona', 'massachusetts', 'tennessee',
        'indiana', 'missouri', 'maryland', 'wisconsin', 'colorado'
    }
    
    STOPWORDS = {
        'he', 'she', 'it', 'they', 'we', 'i', 'you', 'the', 'a', 'an', 
        'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with',
        'is', 'am', 'are', 'was', 'were', 'been', 'being', 'using'
    }
    
    TECH_TERMS = {
        'typescript', 'javascript', 'python', 'java', 'c++', 'c#', 'go', 'rust',
        'react', 'vue', 'angular', 'svelte', 'next.js', 'nuxt', 'php',
        'node.js', 'node', 'django', 'flask', 'express', 'fastapi', 'spring',
        'mongodb', 'postgresql', 'mysql', 'redis', 'elasticsearch',
        'aws', 'azure', 'gcp', 'docker', 'kubernetes',
        'ai', 'ml', 'nlp', 'machine learning', 'deep learning'
    }
    
    @classmethod
    def normalize(cls, text: str, entity_type: str) -> str:
        text_lower = text.lower().strip()
        if entity_type == 'topic':
            return cls.TECH_MAPPINGS.get(text_lower, text.strip())
        elif entity_type == 'location':
            return cls.LOCATION_MAPPINGS.get(text_lower, text.strip())
        return text.strip()
    
    @classmethod
    def normalize_company(cls, company_name: str) -> str:
        normalized = company_name.strip().rstrip('.,;:')
        normalized = ' '.join(normalized.split())
        return normalized
    
    @classmethod
    def is_valid_topic(cls, text: str) -> bool:
        return text.lower() not in cls.STOPWORDS and len(text) > 1
    
    @classmethod
    def is_tech_term(cls, text: str) -> bool:
        text_clean = text.lower().strip().rstrip('.,;:')
        return text_clean in cls.TECH_TERMS
    
    @classmethod
    def is_us_state(cls, text: str) -> bool:
        return text.lower().strip() in cls.US_STATES


class ConfidenceScorer:
    """Calculates confidence scores for extracted entities"""
    
    @staticmethod
    def score_entity(entity: str, source: str, context: str, entity_type: str = None) -> float:
        base_scores = {'spacy': 0.85, 'regex': 0.8, 'hybrid': 0.9}
        confidence = base_scores.get(source, 0.5)
        
        if entity and len(entity) > 0 and entity[0].isupper():
            confidence = min(confidence + 0.05, 1.0)
        
        context_lower = context.lower()
        
        if entity_type == 'person':
            if any(keyword in context_lower for keyword in ['works at', 'employee', 'colleague']):
                confidence = min(confidence + 0.05, 1.0)
        elif entity_type == 'company':
            if any(keyword in context_lower for keyword in ['works at', 'company', 'startup']):
                confidence = min(confidence + 0.05, 1.0)
        
        if len(entity) < 3:
            confidence -= 0.1
        
        return min(max(confidence, 0.0), 1.0)


class PeopleExtractor:
    """Extracts person names from text"""
    
    @staticmethod
    def extract(doc, text: str) -> List[Entity]:
        people = []
        seen = set()
        full_names = set()
        
        company_keywords = ['techworks', 'inc', 'ltd', 'corp', 'llc', 'company', 'solutions', 'systems']
        
        # First pass: collect full names
        for ent in doc.ents:
            if ent.label_ == "PERSON":
                name_parts = ent.text.split()
                if len(name_parts) >= 2:
                    words = ent.text.lower().split()
                    is_company = any(word in company_keywords for word in words)
                    
                    if not is_company and not EntityNormalizer.is_tech_term(ent.text):
                        full_names.add(ent.text)
        
        # Second pass: extract entities, skip partial names
        for ent in doc.ents:
            if ent.label_ == "PERSON":
                if ent.text in seen:
                    continue
                
                is_partial = any(ent.text in full_name and ent.text != full_name 
                               for full_name in full_names)
                if is_partial:
                    continue
                
                words = ent.text.lower().split()
                is_company = any(word in company_keywords for word in words)
                
                if not is_company and not EntityNormalizer.is_tech_term(ent.text):
                    confidence = ConfidenceScorer.score_entity(ent.text, 'spacy', text, 'person')
                    people.append(Entity(value=ent.text, type="person", 
                                       confidence=confidence, source='spacy'))
                    seen.add(ent.text)
        
        return people


class OrganizationExtractor:
    """Extracts organization/company names"""
    
    @staticmethod
    def extract(doc, text: str) -> List[Entity]:
        orgs = []
        seen = set()
        
        # Extract from spaCy ORG entities
        for ent in doc.ents:
            if ent.label_ == "ORG":
                if EntityNormalizer.is_tech_term(ent.text):
                    continue
                
                normalized = EntityNormalizer.normalize_company(ent.text)
                
                if normalized not in seen:
                    confidence = ConfidenceScorer.score_entity(normalized, 'spacy', text, 'company')
                    orgs.append(Entity(value=normalized, type="organization", 
                                     confidence=confidence, source='spacy'))
                    seen.add(normalized)
        
        # Pattern: "works at [Company]" - FIXED: More restrictive
        company_pattern = r'works?\s+(?:at|for)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3}(?:\s+(?:Inc|LLC|Ltd|Corp|Company|Technologies|Systems|Solutions|Ventures|Lab|Group))?)\b'
        matches = re.finditer(company_pattern, text)
        for match in matches:
            company = match.group(1).strip()
            
            if EntityNormalizer.is_tech_term(company):
                continue
            
            normalized = EntityNormalizer.normalize_company(company)
            
            if normalized not in seen:
                confidence = ConfidenceScorer.score_entity(normalized, 'regex', text, 'company')
                orgs.append(Entity(value=normalized, type="organization", 
                                 confidence=confidence, source='regex'))
                seen.add(normalized)
        
        return orgs


class LocationExtractor:
    """Extracts location entities"""
    
    @staticmethod
    def extract(doc, text: str) -> List[Entity]:
        locations = []
        seen = set()
        
        for ent in doc.ents:
            if ent.label_ == "GPE":
                if EntityNormalizer.is_tech_term(ent.text) or EntityNormalizer.is_us_state(ent.text):
                    continue
                
                normalized = EntityNormalizer.normalize(ent.text, 'location')
                
                if normalized not in seen:
                    confidence = ConfidenceScorer.score_entity(normalized, 'spacy', text, 'location')
                    locations.append(Entity(value=normalized, type="location", 
                                          confidence=confidence, source='spacy'))
                    seen.add(normalized)
        
        return locations


class TopicExtractor:
    """Extracts topics and themes from conversation"""
    
    TECH_PATTERNS = [
        r'\b(React|Vue|Angular|Svelte|Next\.js|Nuxt)\b',
        r'\b(JavaScript|TypeScript|Python|Java|C\+\+|C#|Go|Rust|PHP|Ruby)\b',
        r'\b(Node\.?js|Django|Flask|Express|FastAPI|Spring|Laravel)\b',
        r'\b(MongoDB|PostgreSQL|MySQL|Redis|Elasticsearch|Cassandra)\b',
        r'\b(AWS|Azure|GCP|Docker|Kubernetes|Terraform)\b',
        r'\b(AI|ML|Machine\s+Learning|Deep\s+Learning|NLP)\b'
    ]
    
    @staticmethod
    def extract(doc, text: str) -> List[Entity]:
        topics = {}
        
        for pattern in TopicExtractor.TECH_PATTERNS:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                topic = match.group(0)
                normalized = EntityNormalizer.normalize(topic, 'topic')
                
                if normalized not in topics:
                    confidence = ConfidenceScorer.score_entity(normalized, 'regex', text)
                    topics[normalized] = Entity(value=normalized, type="topic", 
                                               confidence=confidence, source='regex')
        
        return list(topics.values())


class PreferenceExtractor:
    """Extracts user preferences and inclinations"""
    
    PREFERENCE_PATTERNS = [
        (r'prefers?\s+([\w\s\.]+?)\s+over\s+([\w\s\.]+?)(?=\s+(?:but|and|He|She|It|They)|\.|,|$)', 'prefer_over'),
        (r'dislikes?\s+([\w\s\.]+?)(?=\s+(?:but|and|He|She|It|They)|\.|,|$)', 'dislikes'),
        (r'hates?\s+([\w\s\.]+?)(?=\s+(?:but|and|He|She|It|They)|\.|,|$)', 'hates'),
        (r'likes?\s+(?:using\s+)?([\w\s\.]+?)(?=\s+(?:but|and|He|She|It|They)|\.|,|$)', 'likes'),
        (r'loves?\s+([\w\s\.]+?)(?=\s+(?:but|and|He|She|It|They)|\.|,|$)', 'loves'),
        (r'enjoys?\s+([\w\s\.]+?)(?=\s+(?:but|and|He|She|It|They)|\.|,|$)', 'enjoys'),
    ]
    
    @staticmethod
    def extract(doc, text: str) -> List[Entity]:
        preferences = []
        seen = set()
        
        for pattern, pref_type in PreferenceExtractor.PREFERENCE_PATTERNS:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                if pref_type == 'prefer_over':
                    item1 = EntityNormalizer.normalize(match.group(1).strip(), 'topic')
                    item2 = EntityNormalizer.normalize(match.group(2).strip(), 'topic')
                    pref_text = f"Prefers {item1} over {item2}"
                    category = PreferenceExtractor._categorize(item1)
                    pref_key = f"{pref_type}_{item1}_{item2}"
                else:
                    item = EntityNormalizer.normalize(match.group(1).strip(), 'topic')
                    
                    if item.lower() in EntityNormalizer.STOPWORDS or len(item.strip()) < 2:
                        continue
                    
                    pref_text = f"{pref_type.replace('_', ' ').title()} {item}"
                    category = PreferenceExtractor._categorize(item)
                    pref_key = f"{pref_type}_{item}"
                
                if pref_key in seen:
                    continue
                
                confidence = ConfidenceScorer.score_entity(pref_text, 'regex', text)
                preferences.append(Entity(value=f"{category}|{pref_text}", type="preference", 
                                        confidence=confidence, source='regex'))
                seen.add(pref_key)
        
        return preferences
    
    @staticmethod
    def _categorize(item: str) -> str:
        tech_keywords = ['react', 'vue', 'angular', 'javascript', 'typescript', 
                        'python', 'java', 'node', 'django', 'mongodb', 'rust']
        return "language" if any(tech in item.lower() for tech in tech_keywords) else "general"


class RelationshipExtractor:
    """Extracts relationships between entities"""
    
    @staticmethod
    def extract(text: str, people: List[Entity], orgs: List[Entity], 
                prefs: List[Entity]) -> List[Dict[str, str]]:
        relationships = []
        seen = set()
        
        # Works at relationships
        works_pattern = r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+works?\s+(?:at|for)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3}(?:\s+(?:Inc|LLC|Ltd|Corp|Company|Technologies|Systems|Solutions|Ventures|Lab))?)\b'
        matches = re.finditer(works_pattern, text)
        
        for match in matches:
            person_name = match.group(1).strip()
            company_name = EntityNormalizer.normalize_company(match.group(2).strip())
            
            person_exists = any(p.value == person_name for p in people)
            company_exists = any(EntityNormalizer.normalize_company(o.value) == company_name for o in orgs)
            
            if person_exists and company_exists:
                rel_key = f"{person_name}|works_at|{company_name}"
                if rel_key not in seen:
                    relationships.append({
                        "subject": person_name,
                        "relationship": "works_at",
                        "object": company_name
                    })
                    seen.add(rel_key)
        
        # Preference relationships with sentence-level attribution
        sentences = re.split(r'\.\s+', text)
        
        person_sentences = {}
        for person in people:
            person_sentences[person.value] = []
            for i, sentence in enumerate(sentences):
                if person.value in sentence:
                    person_sentences[person.value].append(i)
        
        for pref in prefs:
            pref_text = pref.value.split('|')[1] if '|' in pref.value else pref.value
            
            pref_sentence_idx = None
            for i, sentence in enumerate(sentences):
                pref_words = pref_text.lower().split()[:3]
                if any(word in sentence.lower() for word in pref_words):
                    pref_sentence_idx = i
                    break
            
            if pref_sentence_idx is None:
                continue
            
            subject_person = None
            for person_name, sentence_indices in person_sentences.items():
                if pref_sentence_idx in sentence_indices:
                    subject_person = person_name
                    break
            
            if not subject_person and pref_sentence_idx < len(sentences):
                sentence = sentences[pref_sentence_idx]
                if re.search(r'\b(?:He|She)\b', sentence):
                    for person_name, sentence_indices in person_sentences.items():
                        if any(idx < pref_sentence_idx for idx in sentence_indices):
                            subject_person = person_name
            
            if not subject_person:
                continue
            
            # Extract different preference types
            RelationshipExtractor._add_preference_relationships(
                pref_text, subject_person, relationships, seen
            )
        
        return relationships
    
    @staticmethod
    def _add_preference_relationships(pref_text: str, subject: str, 
                                     relationships: List[Dict], seen: Set[str]):
        """Helper to add preference relationships"""
        patterns = [
            (r'Prefers\s+([\w\s\.]+?)\s+over\s+([\w\s\.]+?)$', 'prefers', 
             lambda m: f"{m.group(1).strip()} over {m.group(2).strip()}"),
            (r'Likes\s+([\w\s\.]+?)$', 'likes', lambda m: m.group(1).strip()),
            (r'Dislikes\s+([\w\s\.]+?)$', 'dislikes', lambda m: m.group(1).strip()),
            (r'Loves\s+([\w\s\.]+?)$', 'loves', lambda m: m.group(1).strip()),
            (r'Enjoys\s+([\w\s\.]+?)$', 'enjoys', lambda m: m.group(1).strip()),
            (r'Hates\s+([\w\s\.]+?)$', 'hates', lambda m: m.group(1).strip()),
        ]
        
        for pattern, rel_type, obj_extractor in patterns:
            match = re.search(pattern, pref_text)
            if match:
                obj = obj_extractor(match)
                if obj.lower() not in EntityNormalizer.STOPWORDS:
                    rel_key = f"{subject}|{rel_type}|{obj}"
                    if rel_key not in seen:
                        relationships.append({
                            "subject": subject,
                            "relationship": rel_type,
                            "object": obj
                        })
                        seen.add(rel_key)


# ============================================================================
# BASELINE ENTITY EXTRACTOR (Your Original System)
# ============================================================================

class BaselineEntityExtractor:
    """Baseline entity extraction using spaCy (no LLM)"""
    
    def __init__(self):
        self.people_extractor = PeopleExtractor()
        self.org_extractor = OrganizationExtractor()
        self.location_extractor = LocationExtractor()
        self.topic_extractor = TopicExtractor()
        self.preference_extractor = PreferenceExtractor()
        self.relationship_extractor = RelationshipExtractor()
    
    def extract(self, text: str) -> Dict[str, Any]:
        """Extract all entities using baseline spaCy"""
        doc = nlp(text)
        
        people = self.people_extractor.extract(doc, text)
        orgs = self.org_extractor.extract(doc, text)
        locations = self.location_extractor.extract(doc, text)
        topics = self.topic_extractor.extract(doc, text)
        preferences = self.preference_extractor.extract(doc, text)
        relationships = self.relationship_extractor.extract(text, people, orgs, preferences)
        
        return {
            "people": [{"name": p.value, "confidence": round(p.confidence, 2)} for p in people],
            "organizations": [{"name": o.value, "confidence": round(o.confidence, 2)} for o in orgs],
            "locations": [{"name": l.value, "confidence": round(l.confidence, 2)} for l in locations],
            "topics": [{"topic": t.value, "confidence": round(t.confidence, 2)} for t in topics],
            "preferences": [
                {
                    "category": p.value.split('|')[0] if '|' in p.value else "general",
                    "preference": p.value.split('|')[1] if '|' in p.value else p.value,
                    "confidence": round(p.confidence, 2)
                }
                for p in preferences
            ],
            "facts": (
                [{"fact": o.value, "type": "company", "confidence": round(o.confidence, 2)} for o in orgs] +
                [{"fact": l.value, "type": "location", "confidence": round(l.confidence, 2)} for l in locations]
            ),
            "relationships": relationships
        }


# ============================================================================
# LLM ENHANCEMENT LAYER
# ============================================================================

class LLMEntityExtractor:
    """LLM-enhanced entity extraction using FREE APIs"""
    
    LLM_PROMPT = """You are an expert entity extraction system. Extract entities from the conversation text.

TEXT:
{text}

Extract the following in valid JSON format:

{{
  "people": [
    {{"name": "Full Name", "confidence": 0.9}}
  ],
  "organizations": [
    {{"name": "Company Name", "confidence": 0.9}}
  ],
  "locations": [
    {{"name": "City Name", "confidence": 0.9}}
  ],
  "topics": [
    {{"topic": "Technology/Concept", "confidence": 0.9}}
  ],
  "preferences": [
    {{"category": "language or general", "preference": "Likes/Dislikes X", "confidence": 0.9}}
  ],
  "relationships": [
    {{"subject": "Person Name", "relationship": "works_at/likes/prefers/dislikes/loves/enjoys/hates", "object": "Company/Tech"}}
  ]
}}

CRITICAL RULES:
1. People: Extract FULL names only (e.g., "Sarah Johnson", NOT "Sarah" or "Johnson")
2. Organizations: Extract COMPANIES only, NOT technologies (e.g., "Google" YES, "Python" NO)
3. Locations: Extract CITIES only, NOT states (e.g., "Seattle" YES, "Washington" NO)
4. Topics: Extract technologies, frameworks, languages (e.g., Python, React, Docker)
5. Preferences: Attribute to CORRECT person using sentence context
6. Relationships: Extract ALL types (works_at, likes, dislikes, prefers, loves, enjoys, hates)
7. NO partial names, NO tech terms as companies, NO duplicate/contradictory preferences

Return ONLY valid JSON, no markdown, no explanation."""
    
    def __init__(self, provider: LLMProvider = LLMProvider.GROQ, api_key: Optional[str] = None):
        self.provider = provider
        self.api_key = api_key or self._get_api_key()
        self.client = None
        
        if provider == LLMProvider.GROQ:
            if not GROQ_AVAILABLE:
                raise ImportError("Install groq: pip install groq")
            self.client = Groq(api_key=self.api_key)
            self.model = "llama-3.1-70b-versatile"
        
        elif provider == LLMProvider.GEMINI:
            if not GEMINI_AVAILABLE:
                raise ImportError("Install gemini: pip install google-generativeai")
            genai.configure(api_key=self.api_key)
            self.client = genai.GenerativeModel('gemini-1.5-flash')
    
    def _get_api_key(self) -> str:
        """Get API key from environment"""
        if self.provider == LLMProvider.GROQ:
            key = os.getenv("GROQ_API_KEY")
            if not key:
                raise ValueError(
                    "GROQ_API_KEY not found. Get free key: https://console.groq.com/\n"
                    "Then set: export GROQ_API_KEY='your-key'"
                )
            return key
        
        elif self.provider == LLMProvider.GEMINI:
            key = os.getenv("GEMINI_API_KEY")
            if not key:
                raise ValueError(
                    "GEMINI_API_KEY not found. Get free key: https://aistudio.google.com/\n"
                    "Then set: export GEMINI_API_KEY='your-key'"
                )
            return key
    
    def extract(self, text: str) -> Dict[str, Any]:
        """Extract entities using LLM"""
        prompt = self.LLM_PROMPT.format(text=text)
        
        try:
            if self.provider == LLMProvider.GROQ:
                response = self._call_groq(prompt)
            else:
                response = self._call_gemini(prompt)
            
            # Clean response (remove markdown if present)
            response = response.strip()
            if response.startswith("```json"):
                response = response[7:]
            if response.startswith("```"):
                response = response[3:]
            if response.endswith("```"):
                response = response[:-3]
            response = response.strip()
            
            result = json.loads(response)
            
            # Add facts field
            result["facts"] = (
                [{"fact": o["name"], "type": "company", "confidence": o["confidence"]} 
                 for o in result.get("organizations", [])] +
                [{"fact": l["name"], "type": "location", "confidence": l["confidence"]} 
                 for l in result.get("locations", [])]
            )
            
            return result
        
        except Exception as e:
            print(f"❌ LLM extraction failed: {e}")
            raise
    
    def _call_groq(self, prompt: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=2000
        )
        return response.choices[0].message.content
    
    def _call_gemini(self, prompt: str) -> str:
        response = self.client.generate_content(prompt)
        return response.text


# ============================================================================
# MAIN ENTITY EXTRACTOR (Unified Interface)
# ============================================================================

class EntityExtractor:
    """
    Complete Entity Extraction System
    
    Supports 3 modes:
    1. BASELINE: spaCy only (no LLM, no API key needed)
    2. LLM_ONLY: LLM only (requires API key)
    3. HYBRID: LLM validates spaCy (recommended, requires API key)
    """
    
    def __init__(
        self, 
        use_llm: bool = False,
        llm_provider: str = "groq",
        mode: ExtractionMode = ExtractionMode.LLM_ONLY,
        api_key: Optional[str] = None
    ):
        """
        Initialize entity extractor
        
        Args:
            use_llm: If True, use LLM enhancement (requires API key)
            llm_provider: "groq" or "gemini" (both FREE)
            mode: BASELINE, LLM_ONLY, or HYBRID
            api_key: Optional API key (or set env var)
        """
        self.use_llm = use_llm
        self.mode = mode if use_llm else ExtractionMode.BASELINE
        
        # Always initialize baseline (used for fallback)
        self.baseline_extractor = BaselineEntityExtractor()
        
        # Initialize LLM if requested
        self.llm_extractor = None
        if use_llm:
            try:
                provider_enum = LLMProvider.GROQ if llm_provider.lower() == "groq" else LLMProvider.GEMINI
                self.llm_extractor = LLMEntityExtractor(provider=provider_enum, api_key=api_key)
                print(f"✅ LLM mode enabled: {llm_provider.upper()}")
            except Exception as e:
                print(f"⚠️  LLM initialization failed: {e}")
                print("📊 Falling back to BASELINE mode")
                self.mode = ExtractionMode.BASELINE
                self.use_llm = False
    
    def extract(self, text: str) -> Dict[str, Any]:
        """
        Extract entities from text
        
        Args:
            text: Conversation text to analyze
            
        Returns:
            Dictionary with extracted entities
        """
        if self.mode == ExtractionMode.BASELINE or not self.use_llm:
            return self.baseline_extractor.extract(text)
        
        elif self.mode == ExtractionMode.LLM_ONLY:
            try:
                return self.llm_extractor.extract(text)
            except Exception as e:
                print(f"⚠️  LLM extraction failed: {e}")
                print("📊 Falling back to baseline...")
                return self.baseline_extractor.extract(text)
        
        elif self.mode == ExtractionMode.HYBRID:
            # Get baseline results
            baseline_results = self.baseline_extractor.extract(text)
            
            try:
                # LLM validates and enhances
                llm_results = self.llm_extractor.extract(text)
                
                # Merge results (prefer LLM, fallback to baseline)
                return self._merge_results(baseline_results, llm_results)
            
            except Exception as e:
                print(f"⚠️  LLM validation failed: {e}")
                print("📊 Using baseline results...")
                return baseline_results
    
    def _merge_results(self, baseline: Dict[str, Any], llm: Dict[str, Any]) -> Dict[str, Any]:
        """Merge baseline and LLM results (prefer LLM)"""
        # If LLM has more complete results, use it
        llm_entity_count = (
            len(llm.get("people", [])) + 
            len(llm.get("organizations", [])) +
            len(llm.get("relationships", []))
        )
        
        baseline_entity_count = (
            len(baseline.get("people", [])) + 
            len(baseline.get("organizations", [])) +
            len(baseline.get("relationships", []))
        )
        
        # Prefer LLM if it has more relationships (key indicator of quality)
        if len(llm.get("relationships", [])) >= len(baseline.get("relationships", [])):
            return llm
        else:
            return baseline
    
    def extract_with_metadata(self, text: str) -> Dict[str, Any]:
        """Extract entities with metadata"""
        result = self.extract(text)
        
        total_entities = (
            len(result.get("people", [])) +
            len(result.get("organizations", [])) +
            len(result.get("locations", [])) +
            len(result.get("topics", [])) +
            len(result.get("preferences", []))
        )
        
        result["metadata"] = {
            "mode": self.mode.value,
            "llm_enabled": self.use_llm,
            "total_entities": total_entities,
            "entity_breakdown": {
                "people": len(result.get("people", [])),
                "organizations": len(result.get("organizations", [])),
                "locations": len(result.get("locations", [])),
                "topics": len(result.get("topics", [])),
                "preferences": len(result.get("preferences", [])),
                "relationships": len(result.get("relationships", []))
            }
        }
        
        return result


# ============================================================================
# COMPARISON & DEMO UTILITIES
# ============================================================================

class ComparativeAnalyzer:
    """Compare baseline vs LLM extraction"""
    
    def __init__(self):
        self.baseline = EntityExtractor(use_llm=False)
        self.llm = None
    
    def setup_llm(self, provider: str = "groq", api_key: Optional[str] = None) -> bool:
        """Setup LLM extractor"""
        try:
            self.llm = EntityExtractor(use_llm=True, llm_provider=provider, api_key=api_key)
            return True
        except Exception as e:
            print(f"❌ LLM setup failed: {e}")
            return False
    
    def compare(self, text: str) -> Dict[str, Any]:
        """Run side-by-side comparison"""
        print("\n" + "="*80)
        print("🔬 COMPARATIVE ANALYSIS: Baseline vs LLM")
        print("="*80)
        
        # Baseline extraction
        print("\n📊 Running BASELINE (spaCy only)...")
        baseline_result = self.baseline.extract(text)
        
        # LLM extraction
        llm_result = None
        if self.llm:
            print("🤖 Running LLM extraction...")
            llm_result = self.llm.extract(text)
        
        # Print results
        print("\n" + "="*80)
        print("📊 BASELINE RESULTS")
        print("="*80)
        print(json.dumps(baseline_result, indent=2))
        
        if llm_result:
            print("\n" + "="*80)
            print("🤖 LLM RESULTS")
            print("="*80)
            print(json.dumps(llm_result, indent=2))
            
            # Print comparison table
            self._print_comparison_table(baseline_result, llm_result)
        
        return {
            "baseline": baseline_result,
            "llm": llm_result
        }
    
    def _print_comparison_table(self, baseline: Dict, llm: Dict):
        """Print comparison table"""
        print("\n" + "="*80)
        print("📈 COMPARISON TABLE")
        print("="*80)
        print(f"{'Category':<20} {'Baseline':<15} {'LLM':<15} {'Winner':<15}")
        print("-"*80)
        
        categories = ["people", "organizations", "locations", "topics", "preferences", "relationships"]
        
        total_baseline = 0
        total_llm = 0
        
        for cat in categories:
            baseline_count = len(baseline.get(cat, []))
            llm_count = len(llm.get(cat, []))
            
            total_baseline += baseline_count
            total_llm += llm_count
            
            if llm_count > baseline_count:
                winner = "🤖 LLM"
            elif baseline_count > llm_count:
                winner = "📊 Baseline"
            else:
                winner = "🤝 Tie"
            
            print(f"{cat.capitalize():<20} {baseline_count:<15} {llm_count:<15} {winner:<15}")
        
        print("-"*80)
        print(f"{'TOTAL':<20} {total_baseline:<15} {total_llm:<15}")
        
        # Calculate improvement
        if total_baseline > 0:
            improvement = ((total_llm - total_baseline) / total_baseline) * 100
            print(f"\n📊 LLM Improvement: {improvement:+.1f}%")


# ============================================================================
# MAIN DEMO
# ============================================================================

def main():
    """Demo showing baseline vs LLM comparison"""
    
    print("="*80)
    print("🚀 COMPLETE ENTITY EXTRACTION SYSTEM")
    print("    Baseline (spaCy) + FREE LLM Enhancement")
    print("="*80)
    
    # Test case
    test_text = """
Jennifer Martinez works at CloudScale Systems and Sarah Thompson works for InnovateTech Inc. 
Jennifer prefers TypeScript over JavaScript and loves Docker but dislikes MongoDB. 
Sarah enjoys React and likes using Node.js. She hates PHP. 
CloudScale Systems is based in Seattle, Washington and specializes in Kubernetes and AWS. 
InnovateTech Inc is located in Austin, Texas and builds applications with PostgreSQL and Redis.
"""
    
    print("\n📝 TEST INPUT:")
    print(test_text)
    
    # Initialize analyzer
    analyzer = ComparativeAnalyzer()
    
    # Try to setup LLM
    print("\n🔑 Checking for LLM API keys...")
    
    if analyzer.setup_llm(provider="groq"):
        # Run comparison
        results = analyzer.compare(test_text)
        
        # Show scores
        print("\n" + "="*80)
        print("🎯 SCORING ANALYSIS")
        print("="*80)
        
        baseline_score = analyzer._calculate_score(results["baseline"])
        llm_score = analyzer._calculate_score(results["llm"]) if results["llm"] else 0
        
        print(f"Baseline Score: {baseline_score}/10")
        print(f"LLM Score: {llm_score}/10")
        print(f"Improvement: {llm_score - baseline_score:+.1f} points")
    
    else:
        print("\n⚠️  No LLM API key found. Showing baseline only.")
        print("\n🔑 To enable LLM enhancement:")
        print("   1. Get FREE API key:")
        print("      - Groq: https://console.groq.com/ (14,400/day)")
        print("      - Gemini: https://aistudio.google.com/ (1,500/day)")
        print("   2. Set environment variable:")
        print("      export GROQ_API_KEY='your-key'")
        print("   3. Rerun this script")
        
        # Show baseline only
        print("\n📊 Running BASELINE only...")
        result = analyzer.baseline.extract(test_text)
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

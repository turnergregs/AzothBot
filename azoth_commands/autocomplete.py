from supabase_client import (
	get_card_element_choices, 
	get_card_attribute_choices, 
	get_card_type_choices, 
	get_deck_type_choices, 
	get_deck_content_type_choices,
	get_ritual_type_choices,
	get_difficulty_choices
)
choices = {
	"card_element": get_card_element_choices,
	"card_attributes": get_card_attribute_choices,
	"card_type": get_card_type_choices,
	"deck_type": get_deck_type_choices,
	"deck_content_type": get_deck_content_type_choices,
	"ritual_type": get_ritual_type_choices,
	"difficulty": get_difficulty_choices
}

def autocomplete_from_choices(field: str, input: str) -> list[str]:
	func = choices.get(field)
	if not func:
		return []

	options = func()
	return [c for c in options if input.lower() in c.lower()]

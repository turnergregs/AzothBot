from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageChops
import json
import os
import re
from pathlib import Path
import math
import numpy as np
from PIL.ImageSequence import Iterator


FONT_PATH = os.path.join("assets", "fonts", "Aldrich-Regular.ttf")

DOWNLOAD_DIR = os.path.join("assets", "downloaded_images")


class RitualRenderer:
    def __init__(self, ppi=900, bleed_mm=8.5):
        # Standard playing card size is 63.5mm x 88.9mm
        self.card_width_mm = 85.9
        self.card_height_mm = 60.5
        self.bleed_mm = bleed_mm
        self.ppi = min(max(300, ppi), 900)  # Constrain between 300 and 900 ppi

        # Convert measurements to pixels
        mm_to_inch = 0.0393701
        self.px_per_mm = self.ppi * mm_to_inch
        self.width = round((self.card_width_mm + (2 * self.bleed_mm)) * self.px_per_mm)
        self.height = round((self.card_height_mm + (2 * self.bleed_mm)) * self.px_per_mm)
        self.height = 2448
        self.width = 3330

        self.border_width = 40  # Fixed 10px border width

        # Load fonts
        base_size = round(self.height / 16)  # Starting title font size
        self.cost_font = ImageFont.truetype(FONT_PATH, base_size)
        self.text_font = ImageFont.truetype(FONT_PATH, round(base_size * 0.9))
        self.sub_font = ImageFont.truetype(FONT_PATH, round(base_size * 0.7))
        # Colors
        # self.light_mode = {
        #     'border': (0, 0, 0, 255),
        #     'text': (0, 0, 0, 255),
        #     'background': (255, 255, 255, 255),
        #     'cost': (0, 0, 0, 255)
        # }
        self.light_mode = {
            'border': (225, 225, 225, 255),
            'text': (225, 225, 225, 255),
            'background': (12, 12, 12, 255),
            'background_empty': (12, 12, 12, 0),
            'cost': (12, 12, 12, 255)
        }

        self.dark_mode = {
            'border': (255, 255, 255, 255),
            'text': (255, 255, 255, 255),
            'darktext': (12, 12, 12, 255),
            'background': (12, 12, 12, 255),
            'background_empty': (12, 12, 12, 0),
            'cost': (12, 12, 12, 255)
        }

        self.element_colors = {
            'blood': (255, 0, 0, 255),  # Red
            'sol': (249, 164, 16, 255),  # Gold
            'anima': (135, 105, 233, 255),  # Purple
            'none': (225, 225, 225, 255),
        }

    def get_predominant_color(self, image):
        """
        Determine if black or white is predominant in an RGBA image.
        Assumes image is a numpy array with shape (height, width, 4) for RGBA.
        """
        # Check if pixels are black (0,0,0,255) or white (255,255,255,255)
        black_pixels = np.all(image[:, :, :3] <= 14, axis=2) & (image[:, :, 3] == 255)
        white_pixels = np.all(image[:, :, :3] == 255, axis=2) & (image[:, :, 3] == 255)

        black_count = np.sum(black_pixels)
        white_count = np.sum(white_pixels)

        total_pixels = image.shape[0] * image.shape[1]
        if black_count > white_count or black_count + white_count <= 10000:
            return "black", (black_count / total_pixels) * 100
        else:
            return "white", (white_count / total_pixels) * 100


    def set_black__white_to_off_black_white(self, image, dark_mode, threshold=25):
        """
        Invert black and white pixels in an RGBA image, using a threshold to catch near-black pixels.

        Args:
            image: RGBA numpy array
            threshold: Integer 0-255. Pixels with RGB values all below this are considered "black"
                      Higher threshold will catch more dark grays
        """
        result = image.copy()

        # First identify pixels with sufficient alpha (opacity)
        colored_pixels = image[:, :, 3] >= threshold

        # Create boolean masks for the entire image
        black_mask = np.zeros_like(colored_pixels, dtype=bool)
        white_mask = np.zeros_like(colored_pixels, dtype=bool)

        # Find positions where colored pixels are near-black
        # We need to use colored_pixels to index into our mask arrays
        black_mask[colored_pixels] = np.any(image[colored_pixels][:, 0:3] <= threshold, axis=1)

        # Find positions where colored pixels are near-white
        white_mask[colored_pixels] = np.any(image[colored_pixels][:, 0:3] >= 255 - threshold, axis=1)

        if dark_mode:
            colors = self.dark_mode
        else:
            colors = self.light_mode

        # Apply the transformations
        result[black_mask] = colors['background']  # Set black pixels to dark gray
        result[white_mask] = colors['text']  # Set white pixels to light gray

        # result2 = image.copy()

        # First identify pixels with sufficient alpha (opacity)
        colored_pixels = image[:, :, 3] <= threshold

        # Create boolean masks for the entire image
        black_mask = np.zeros_like(colored_pixels, dtype=bool)
        white_mask = np.zeros_like(colored_pixels, dtype=bool)

        # Find positions where colored pixels are near-black
        # We need to use colored_pixels to index into our mask arrays
        black_mask[colored_pixels] = np.any(image[colored_pixels][:, 0:3] <= threshold, axis=1)

        # Find positions where colored pixels are near-white
        white_mask[colored_pixels] = np.any(image[colored_pixels][:, 0:3] >= 255 - threshold, axis=1)

        if dark_mode:
            colors = self.dark_mode
        else:
            colors = self.light_mode

        # Apply the transformations
        result[black_mask] = colors['background_empty']  # Set black pixels to dark gray
        result[white_mask] = colors['text']  # Set white pixels to light gray

        return result

    def invert_black_white(self, image, threshold=100):
        """
        Invert black and white pixels in an RGBA image, using a threshold to catch near-black pixels.

        Args:
            image: RGBA numpy array
            threshold: Integer 0-255. Pixels with RGB values all below this are considered "black"
                      Higher threshold will catch more dark grays
        """
        result = image.copy()

        # Find near-black pixels (all RGB values below threshold)
        # We only look at RGB channels (not alpha) when determining darkness
        black_pixels = np.all(image[:, :, :3] <= threshold, axis=2)

        # Find white pixels (keeping original strict white detection)
        white_pixels = np.all(image[:, :, :3] >= 255- threshold, axis=2)

        # Set black/dark pixels to white
        result[black_pixels] = [255, 255, 255, 255]
        # Set white pixels to black
        result[white_pixels] = [0, 0, 0, 255]

        return result

    def draw_right_side_label(self, cropped_image, text, image, dark=True):
        target_size = [180, 180]

        def maintain_aspect_ratio(image, target_size):
            original_width, original_height = image.size
            target_width, target_height = target_size

            # Calculate aspect ratios
            original_aspect = original_width / original_height
            target_aspect = target_width / target_height

            if target_aspect > original_aspect:
                # Width is the constraining factor
                new_width = target_height * original_aspect
                new_height = target_height
            else:
                # Height is the constraining factor
                new_width = target_width
                new_height = target_width / original_aspect

            return image.resize((int(new_width), int(new_height)), Image.LANCZOS)
        # Resize maintaining aspect ratio


        # target_with_padding = (target_size[0] , target_size[1])
        icon = maintain_aspect_ratio(cropped_image, target_size)

        spacing = 50
        x_position = [217, 2950]
        temp_img = Image.new('RGBA', (1, 1), (255, 255, 255, 0))
        temp_draw = ImageDraw.Draw(temp_img)
        bbox = temp_draw.textbbox((0, 0), text, font=self.sub_font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]

        # Calculate total width and height before rotation
        total_width = icon.width + spacing + text_width
        total_height = max(icon.height, text_height)

        temp_img = Image.new('RGBA', (total_width, total_height), (255, 255, 255, 0))

        # Place icon at the left
        icon_y = int((total_height - icon.height*0.9) / 2)
        temp_img.paste(icon, (0, icon_y), icon if icon.mode == 'RGBA' else None)

        # Place text next to icon
        text_x = icon.width + spacing
        text_y = (total_height - text_height) // 2
        temp_draw = ImageDraw.Draw(temp_img)

        if dark:

            self.draw_semibold_text(temp_draw, text, text_x, text_y, font = self.sub_font, fill_color='white')
            # temp_draw.text((text_x, text_y), text, fill='white', font=self.sub_font)

        else:
            self.draw_semibold_text(temp_draw, text, text_x, text_y, font=self.sub_font, fill_color='black')
            # temp_draw.text((text_x, text_y), text, fill='black', font=self.sub_font)

        # Rotate the combined image 90 degrees counterclockwise
        rotated_img = temp_img.rotate(90, expand=True)



        rotated_height = rotated_img.height
        base_height = image.height
        y_position = int(base_height * 0.69 - rotated_height + target_size[1]/4)

        # Paste the rotated image onto the base image
        image.paste(rotated_img, (x_position[1], y_position), rotated_img)

        # import matplotlib.pyplot as plt
        #
        # plt.imshow(image)
        # plt.show()

        return image

    def draw_left_side_label(self, cropped_image, text, image):

        target_size = [180, 180]

        def maintain_aspect_ratio(image, target_size):
            original_width, original_height = image.size
            target_width, target_height = target_size

            # Calculate aspect ratios
            original_aspect = original_width / original_height
            target_aspect = target_width / target_height

            if target_aspect > original_aspect:
                # Width is the constraining factor
                new_width = target_height * original_aspect
                new_height = target_height
            else:
                # Height is the constraining factor
                new_width = target_width
                new_height = target_width / original_aspect

            return image.resize((int(new_width), int(new_height)), Image.LANCZOS)
        # Resize maintaining aspect ratio


        # target_with_padding = (target_size[0] , target_size[1])
        icon = maintain_aspect_ratio(cropped_image, target_size)

        spacing = 50
        x_position = [217, 3000]

        temp_img = Image.new('RGBA', (1, 1), (255, 255, 255, 0))
        temp_draw = ImageDraw.Draw(temp_img)
        bbox = temp_draw.textbbox((0, 0), text, font=self.sub_font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]

        # Calculate total width and height before rotation
        total_width = icon.width + spacing + text_width
        total_height = max(icon.height, text_height)

        temp_img = Image.new('RGBA', (total_width, total_height), (255, 255, 255, 0))

        # Place icon at the left
        icon_y = int((total_height - icon.height*1.1) / 2)
        temp_img.paste(icon, (0, icon_y), icon if icon.mode == 'RGBA' else None)

        # Place text next to icon
        text_x = icon.width + spacing
        text_y = (total_height - text_height) // 2
        temp_draw = ImageDraw.Draw(temp_img)
        temp_draw.text((text_x, text_y), text, fill='white', font=self.sub_font)

        # Rotate the combined image 90 degrees counterclockwise
        rotated_img = temp_img.rotate(90, expand=True)



        rotated_height = rotated_img.height
        base_height = image.height
        y_position = int(base_height * 0.69 - rotated_height + target_size[1]/4)

        # Paste the rotated image onto the base image
        image.paste(rotated_img, (x_position[0], y_position), rotated_img)

        # import matplotlib.pyplot as plt
        #
        # plt.imshow(image)
        # plt.show()

        return image


    def process_frame(self, input_image, target_size, padding_ratio=0.16, card_data=None, is_dark_mode = True):
        """Process a single frame: find pattern bounds, crop to square, and resize with padding"""
        # Convert to RGB if not already
        if input_image.mode != 'RGBA':
            input_image = input_image.convert('RGB')

        # Convert image to numpy array for processing
        img_array = np.array(input_image)
        # is_dark_mode = True
        background_color, _ = self.get_predominant_color(img_array)
        if is_dark_mode:
            # if background_color == "white":
            #     img_array = self.invert_black_white(img_array)

            pattern_mask = img_array[:, :, 3] > 0
        else:
            # if background_color == 'black':
            # img_array = self.invert_black_white(img_array)
            pattern_mask = np.any(img_array[:, :, 0:3] < 255, axis=2)

        img_array = self.set_black__white_to_off_black_white(img_array, is_dark_mode)


        input_image = Image.fromarray(img_array)

        rows = np.any(pattern_mask, axis=1)
        cols = np.any(pattern_mask, axis=0)


        # Get the pattern boundaries
        ymin, ymax = np.where(rows)[0][[0, -1]]
        xmin, xmax = np.where(cols)[0][[0, -1]]

        # Add small padding around the pattern (10% of pattern size)
        pattern_height = ymax - ymin
        pattern_width = xmax - xmin
        padding = int(max(pattern_height, pattern_width) * 0.1)

        # Expand the bounds with padding
        ymin = max(0, ymin - padding)
        ymax = min(input_image.height, ymax + padding)
        xmin = max(0, xmin - padding)
        xmax = min(input_image.width, xmax + padding)

        # Calculate the square bounds
        pattern_size = max(ymax - ymin, xmax - xmin)

        # Ensure the square is centered on the pattern
        x_center = (xmin + xmax) // 2
        y_center = (ymin + ymax) // 2

        # Calculate square bounds
        half_size = pattern_size // 2
        square_xmin = max(0, x_center - half_size)
        square_ymin = max(0, y_center - half_size)
        square_xmax = min(input_image.width, square_xmin + pattern_size)
        square_ymax = min(input_image.height, square_ymin + pattern_size)

        # Ensure we maintain a square even if we hit image boundaries
        width = square_xmax - square_xmin
        height = square_ymax - square_ymin
        if width != height:
            new_size = max(width, height)
            if width < new_size:
                diff = new_size - width
                square_xmin = max(0, square_xmin - diff // 2)
                square_xmax = min(input_image.width, square_xmin + new_size)
                if square_xmax - square_xmin < new_size:  # Hit right boundary
                    square_xmin = max(0, square_xmax - new_size)
            if height < new_size:
                diff = new_size - height
                square_ymin = max(0, square_ymin - diff // 2)
                square_ymax = min(input_image.height, square_ymin + new_size)
                if square_ymax - square_ymin < new_size:  # Hit bottom boundary
                    square_ymin = max(0, square_ymax - new_size)

        # Crop to the square bounds
        cropped_image = input_image.crop((square_xmin, square_ymin, square_xmax, square_ymax))
        # import matplotlib.pyplot as plt
        # plt.imshow(cropped_image)
        # plt.show()


        # self.draw_left_side_label(cropped_image, card_data, )

        def maintain_aspect_ratio(image, target_size):
            original_width, original_height = image.size
            target_width, target_height = target_size

            # Calculate aspect ratios
            original_aspect = original_width / original_height
            target_aspect = target_width / target_height

            if target_aspect > original_aspect:
                # Width is the constraining factor
                new_width = target_height * original_aspect
                new_height = target_height
            else:
                # Height is the constraining factor
                new_width = target_width
                new_height = target_width / original_aspect

            return image.resize((int(new_width), int(new_height)), Image.LANCZOS)
        # Resize maintaining aspect ratio

        padding = round(target_size[0] * padding_ratio)
        target_with_padding = (target_size[0] - 2 * padding, target_size[1] - 2 * padding)
        sized_image  = maintain_aspect_ratio(cropped_image, target_with_padding)

        # cropped_image.save("icons/" + card_data['name'].replace(" ", "_") + '_icon.png')
        # import matplotlib.pyplot as plt
        # plt.imshow(sized_image)
        # plt.show()

        # Create transparent base image
        final_image = Image.new('RGBA', (target_size[0], target_size[1]), (12, 12, 12, 0))

        # Calculate text dimensions if card_data is provided
        margin = round(self.px_per_mm * self.bleed_mm)
        text_offset = 0

        if card_data and 'text' in card_data:
            # Get text dimensions to adjust image position
            card_text = card_data['text']
            text_box_width = target_size[0] - (2 * margin) - (4 * self.border_width) - 2 * self.px_per_mm * 3

            # Helper function to calculate wrapped lines
            def get_wrapped_lines(text, font, max_width):
                words = text.split()
                lines = []
                current_line = []

                for word in words:
                    test_line = ' '.join(current_line + [word])
                    bbox = font.getbbox(test_line)
                    width = bbox[2] - bbox[0]

                    if width <= max_width:
                        current_line.append(word)
                    else:
                        if current_line:
                            lines.append(' '.join(current_line))
                        current_line = [word]

                if current_line:
                    lines.append(' '.join(current_line))
                return lines

            # Calculate number of lines
            wrapped_lines = get_wrapped_lines(card_text, self.text_font, text_box_width)
            line_count = len(wrapped_lines)

            # Calculate offset based on line count
            text_offset = min(line_count * 20, 100)  # Adjust these values to control the shift

        text_offset += 100

        # Calculate paste position with text offset
        paste_x = (target_size[0] - sized_image.size[0]) // 2
        paste_y = (target_size[1] - sized_image.size[1]) // 2 - text_offset

        # Create a rounded rectangle mask
        mask = Image.new('L', (target_size[0], target_size[1]), 0)
        mask_draw = ImageDraw.Draw(mask)
        corner_radius = round(target_size[0] * 0.05)  # Match the card corner radius

        # Draw rounded rectangle on mask
        mask_draw.rounded_rectangle(
            [0, 0, target_size[0], target_size[1]],
            corner_radius,
            fill=255
        )

        is_dark_mode = True
        # is_dark_mode = card_data['dark']
        # Create a new image with the cropped content and apply the mask
        if is_dark_mode:
            temp_image = Image.new('RGBA', (target_size[0], target_size[1]), (12, 12, 12, 0))
        else:
            temp_image = Image.new('RGBA', (target_size[0], target_size[1]), (225, 225, 225, 0))
        temp_image.paste(sized_image, (paste_x, paste_y))
        temp_image.putalpha(mask)

        # import matplotlib.pyplot as plt
        # plt.imshow(temp_image)
        # plt.show()
        # plt.show()
        # Paste the masked image onto the final transparent image
        final_image = Image.alpha_composite(final_image, temp_image)

        final_image = self.set_black__white_to_off_black_white(np.array(final_image), is_dark_mode)


        # import matplotlib.pyplot as plt
        # plt.imshow(final_image)
        # plt.show()
        # import matplotlib.pyplot as plt



        return Image.fromarray(final_image), (square_xmin, square_ymin, square_xmax, square_ymax), (paste_x, paste_y), cropped_image

    def draw_wrapped_text(self, draw, text, x, y, box_width, box_height, start_font_size, font_path, fill_color,
                          alignment='center'):
        """
        Draw wrapped text that scales to fit within a given box, at the specified position.

        Parameters:
            draw: ImageDraw object
            text: Text to draw
            x: X coordinate of the reference point (depends on alignment)
            y: Y coordinate of the reference point (center of text block)
            box_width: Maximum width of the text box
            box_height: Maximum height of the text box
            start_font_size: Initial font size to try
            font_path: Path to the font file
            fill_color: Color of the text
            alignment: Text alignment ('left', 'center', or 'right')
        """

        def get_wrapped_lines(text, font, max_width):
            words = text.split()
            lines = []
            current_line = []

            for word in words:
                # Handle case where a single word is longer than the box width
                if not current_line and font.getbbox(word)[2] > max_width:
                    # Add word anyway and let it overflow slightly
                    lines.append(word)
                    continue

                test_line = ' '.join(current_line + [word])
                width = font.getbbox(test_line)[2] - font.getbbox(test_line)[0]

                if width <= max_width:
                    current_line.append(word)
                else:
                    if current_line:
                        lines.append(' '.join(current_line))
                    current_line = [word]

            if current_line:
                lines.append(' '.join(current_line))

            return lines

        def compute_text_height(lines, font):
            sample_bbox = font.getbbox('Ay')
            line_height = sample_bbox[3] - sample_bbox[1]
            line_spacing = line_height * 0.2
            return len(lines) * (line_height + line_spacing)

        # Auto-scale font size
        font_size = start_font_size
        min_font_size = 12

        while font_size >= min_font_size:
            font = ImageFont.truetype(font_path, font_size)
            lines = get_wrapped_lines(text, font, box_width)
            total_height = compute_text_height(lines, font)

            if total_height <= box_height:
                break

            font_size -= 1

        font = ImageFont.truetype(font_path, font_size)
        lines = get_wrapped_lines(text, font, box_width)

        # Get line height for positioning
        sample_bbox = font.getbbox('Ay')
        line_height = sample_bbox[3] - sample_bbox[1]
        line_spacing = line_height * 0.2

        # Calculate total text block height
        total_text_height = len(lines) * (line_height + line_spacing)

        # Start at the specified y position
        current_y = y - (total_text_height / 2)  # Center the text block vertically around the specified y

        # Draw each line with specified alignment
        for line in lines:
            bbox = font.getbbox(line)
            line_width = bbox[2] - bbox[0]

            # Determine x position based on alignment
            if alignment == 'left':
                line_x = x
            elif alignment == 'right':
                line_x = x - line_width
            else:  # center alignment
                line_x = x - (line_width / 2)

            draw.text((line_x, current_y), line, font=font, fill=fill_color)
            current_y += line_height + line_spacing

    def render_card_sides(self, draw, card_data, margin, colors):
        """Render both the challenge and bonus sides of the card."""

        # Common settings
        xoffset_from_middle = 200  # Distance from center
        text_box_width = self.width / 1.8 - (2 * margin) - (4 * self.border_width) - 2 * self.px_per_mm * 3
        text_box_height = self.height * 0.3
        base_size = round(self.width / 25.0)

        # Define sides configuration
        sides = [
            {
                'side_key': 'challenge',
                'x_offset': -xoffset_from_middle,  # Left of center
                'color': colors['text'],
                'alignment': 'right',
                'label': "Ritual"
            },
            {
                'side_key': 'reward',
                'x_offset': xoffset_from_middle,  # Right of center
                'color': colors['darktext'],
                'alignment': 'left',
                'label': "Reward"
            }
        ]

        # Render each side
        for side_config in sides:
            side_name = side_config["side_key"]

            # Calculate positions
            center_x = self.width / 2
            title_x = center_x + side_config['x_offset']
            text_x = center_x + side_config['x_offset']
            label_x = int(center_x + np.sign(side_config['x_offset'])*475 * 1.35)

            # Render label
            self.render_card_title(
                draw,
                side_config['label'],
                label_x,
                self.height * 0.20,
                base_size,
                FONT_PATH,
                text_box_width,
                side_config['color'],
                'center'
            )


            # Render title
            self.render_card_title(
                draw,
                card_data[f"{side_name}_name"],
                title_x,
                self.height * 0.69,
                base_size,
                FONT_PATH,
                text_box_width,
                side_config['color'],
                side_config['alignment']
            )

            # Render description text
            self.draw_wrapped_text(
                draw,
                card_data[f"{side_name}_text"],
                text_x,
                self.height * 0.78,
                text_box_width,
                text_box_height,
                int(self.text_font.size / 1.5),
                FONT_PATH,
                side_config['color'],
                side_config['alignment']
            )

    def render_card_title(self, draw, name_text, x, y, base_size, font_path, max_width, fill_color, alignment='center'):
        """
        Render a card title with auto-scaling to fit within max_width.
        Aligns text by baseline to ensure consistent visual alignment.

        Parameters:
            draw: ImageDraw object
            name_text: Title text to render
            x: X coordinate (based on alignment)
            y: Y coordinate (represents the baseline position)
            base_size: Starting font size
            font_path: Path to font file
            max_width: Maximum allowed width
            fill_color: Text color
            alignment: Text alignment ('left', 'center', or 'right')
        """
        # Start with default font size and try progressively smaller sizes
        min_size = round(base_size * 0.5)
        current_font = None

        # # Try sizes from largest to smallest until finding one that fits
        # for size in range(base_size, min_size - 1, -1):
        #     test_font = ImageFont.truetype(font_path, size)
        #     test_bbox = draw.textbbox((0, 0), name_text, font=test_font)
        #     test_width = test_bbox[2] - test_bbox[0]
        #
        #     if test_width <= max_width:
        #         current_font = test_font
        #         break

        # If no suitable size found, use minimum size
        if current_font is None:
            current_font = ImageFont.truetype(font_path, int(base_size * 1.15))

        # Calculate the baseline offset by using a character without descenders
        baseline_bbox = draw.textbbox((0, 0), "ABCDEFHIJKLMNOPRSTUVWXYZ", font=current_font)
        baseline_height = baseline_bbox[3]  # Bottom of capital letters = baseline

        # Calculate the actual text bbox
        text_bbox = draw.textbbox((0, 0), name_text, font=current_font)
        text_width = text_bbox[2] - text_bbox[0]

        # Calculate text position
        # The y parameter now represents where we want the baseline to be
        if alignment == 'left':
            text_x = x
        elif alignment == 'right':
            text_x = x - text_width
        else:  # center
            text_x = x - (text_width / 2)

        # Adjust y position to place the baseline at the specified y coordinate
        # We need to subtract the baseline height from y to get the top position for drawing
        text_y = y - baseline_height

        # Draw the text with semibold effect
        self.draw_semibold_text(draw, name_text, text_x, text_y, current_font, fill_color)

    def draw_semibold_text(self, draw, text, x, y, font, fill_color, semibold_strength=1):
        """
        Draw text with a semibold effect by drawing it multiple times with small offsets.

        Parameters:
            draw: ImageDraw object
            text: Text to draw
            x, y: Position
            font: Font to use
            fill_color: Text color
            semibold_strength: Strength of the semibold effect (1-3)
        """
        # Draw multiple times with small offsets to create a semibold effect
        offsets = [(0, 0)]

        # Add more offsets based on semibold_strength
        if semibold_strength >= 1:
            offsets.extend([(1, 0), (-1, 0), (0, 1), (0, -1)])
        if semibold_strength >= 2:
            offsets.extend([(1, 1), (-1, -1), (1, -1), (-1, 1)])
        if semibold_strength >= 3:
            offsets.extend([(2, 0), (-2, 0), (0, 2), (0, -2)])

        for dx, dy in offsets:
            draw.text((x + dx, y + dy), text, font=font, fill=fill_color)

    def draw_view_shape(self, draw, image, center_x, center_y):
        """Draw the cost shape based on the element type."""

        # Load the appropriate icon based on element
        icon_path = os.path.join("assets", "icons", "view.png")
        icon = Image.open(icon_path)

        # Convert to RGBA if it isn't already
        if icon.mode != 'RGBA':
            icon = icon.convert('RGBA')

        # Calculate the desired size (diameter of the original circle)
        # icon_size = int(radius * 2)

        # Resize the icon while maintaining aspect ratio
        # icon.thumbnail((int(icon.size[0]*0.9), int(icon.size[1]*0.9)), Image.LANCZOS)

        # Calculate position to center the icon
        paste_x = int(center_x - icon.width / 2)
        paste_y = int(center_y - icon.height / 2)

        # Create a copy of the main image
        # temp_image = image.copy()

        # Create a temporary image for compositing
        temp = Image.new('RGBA', image.size, (0, 0, 0, 0))

        # Paste the icon onto the temporary image
        temp.paste(icon, (paste_x, paste_y), icon)
        temparray = np.array(temp)


        temparray[:, :, 3] = np.minimum(image.split()[3], temparray[:, :, 3])

        icon = Image.fromarray(temparray)

        main_image = image
        main_image.paste(icon, (0, 0), icon)


    def render_ritual_card(self, card_data, processed_frames, image_area, transparent_outside, margin, colors, image_margin, output_dir, base_filename, output_path):


        if 'challenge_image' in card_data and card_data["challenge_image"] and 'reward_image' in card_data and card_data["reward_image"]:
            if True:
                source_image = Image.open(os.path.join(DOWNLOAD_DIR, "rituals", card_data["challenge_image"]))
                is_animated = hasattr(source_image, 'is_animated') and source_image.is_animated

                # Process first frame to get parameters
                first_frame = source_image.copy()
                if is_animated:
                    first_frame.seek(0)
                challenge_frame, crop_params, paste_pos, cropped_image1 = self.process_frame(first_frame, image_area, card_data=card_data)
                # processed_frames.append(processed_frame)


                source_image = Image.open(os.path.join(DOWNLOAD_DIR, "rituals", card_data["reward_image"]))
                first_frame = source_image.copy()
                bonus_frame, crop_params, paste_pos, cropped_image2 = self.process_frame(first_frame, image_area, card_data=card_data, is_dark_mode=False)

                # # Process remaining frames if animated
                # if is_animated:
                #     for frame_idx in range(1, source_image.n_frames):
                #         source_image.seek(frame_idx)
                #         frame = source_image.copy()
                #         # Use the same processing function with the same parameters
                #         processed_frame, _, _, cropped_images = self.process_frame(
                #             frame.crop(crop_params),  # Apply initial crop
                #             image_area,  # Same target size
                #             padding_ratio=0.1,  # Same padding
                #             card_data = card_data
                #         )
                #         processed_frames.append(processed_frame)


        def render_single_frame(challenge_image, bonus_image):
            if transparent_outside:
                # Create fully transparent base image
                image = Image.new('RGBA', (self.width, self.height), (0, 0, 0, 0))

                # Create a mask for the rounded rectangle with exact same parameters as border
                mask = Image.new('L', (self.width, self.height), 0)
                mask_draw = ImageDraw.Draw(mask)

                # Use same radius and coordinates as the border
                corner_radius = round(self.width * 0.05)
                rect_bounds = [margin, margin, self.width - margin, self.height - margin]

                # Draw the mask slightly smaller than the border to prevent bleeding
                inset = self.border_width // 2
                mask_bounds = [
                    rect_bounds[0] + inset,
                    rect_bounds[1] + inset,
                    rect_bounds[2] - inset,
                    rect_bounds[3] - inset
                ]
                mask_draw.rounded_rectangle(
                    mask_bounds,
                    corner_radius,
                    fill=255
                )

                # Create background color layer
                bg_layer = Image.new('RGBA', (self.width, self.height), colors['background'])
                # Apply the mask to the background
                bg_layer.putalpha(mask)

                # Composite the background onto the transparent base
                image = Image.alpha_composite(image, bg_layer)
            else:
                # Create fully transparent base image
                image = Image.new('RGBA', (self.width, self.height), (0, 0, 0, 0))

                # Create masks for the rounded rectangle
                mask = Image.new('L', (self.width, self.height), 0)
                mask_draw = ImageDraw.Draw(mask)

                # Use same radius and coordinates as the border
                corner_radius = round(self.width * 0.05)
                rect_bounds = [margin / 3.2, margin / 3.2, self.width - margin / 3.2, self.height - margin / 3.2]

                # Draw the mask slightly smaller than the border to prevent bleeding
                inset = self.border_width // 2
                mask_bounds = [
                    rect_bounds[0] + inset,
                    rect_bounds[1] + inset,
                    rect_bounds[2] - inset,
                    rect_bounds[3] - inset
                ]

                # Draw the full rounded rectangle mask
                mask_draw.rounded_rectangle(
                    mask_bounds,
                    corner_radius,
                    fill=255
                )

                # Calculate the middle x-coordinate for splitting the rectangle
                middle_x = int((mask_bounds[0] + mask_bounds[2]) / 2)

                # Create a mask for splitting
                split_mask = Image.new('L', (self.width, self.height), 0)
                split_draw = ImageDraw.Draw(split_mask)
                split_draw.rectangle([0, 0, middle_x, self.height], fill=255)

                # Create separate masks for left and right sides
                left_mask = mask.copy()
                left_mask = ImageChops.multiply(left_mask, split_mask)

                right_mask = mask.copy()
                right_mask = ImageChops.multiply(right_mask, ImageOps.invert(split_mask))

                # Create background color layers
                left_bg_layer = Image.new('RGBA', (self.width, self.height), colors['background'])
                left_bg_layer.putalpha(left_mask)

                right_bg_layer = Image.new('RGBA', (self.width, self.height), colors['text'])
                right_bg_layer.putalpha(right_mask)

                # Composite both halves onto the transparent base
                image = Image.alpha_composite(image, left_bg_layer)
                image = Image.alpha_composite(image, right_bg_layer)

            draw = ImageDraw.Draw(image)

            # Place processed image if available
            if True:
                # Convert frame_image back to RGBA if it's not already


                if challenge_image.mode != 'RGBA':
                    challenge_image = challenge_image.convert('RGBA')
                if bonus_image.mode != 'RGBA':
                    bonus_image = bonus_image.convert('RGBA')
                # import matplotlib.pyplot as plt
                # plt.imshow(cropped_image1[0])
                # plt.show()
                # Simple paste without using the image as its own mask

                target_size = [950, 950]

                def maintain_aspect_ratio(image, target_size):
                    original_width, original_height = image.size
                    target_width, target_height = target_size

                    # Calculate aspect ratios
                    original_aspect = original_width / original_height
                    target_aspect = target_width / target_height

                    if target_aspect > original_aspect:
                        # Width is the constraining factor
                        new_width = target_height * original_aspect
                        new_height = target_height
                    else:
                        # Height is the constraining factor
                        new_width = target_width
                        new_height = target_width / original_aspect

                    return image.resize((int(new_width), int(new_height)), Image.LANCZOS)

                # Resize maintaining aspect ratio

                # target_with_padding = (target_size[0] , target_size[1])
                cropped_image2 = maintain_aspect_ratio(bonus_image, target_size)
                cropped_image1 = maintain_aspect_ratio(challenge_image, target_size)


                w2, h2 = cropped_image2.size

                w1,h1 = cropped_image1.size
                #side algigned
                image.paste(cropped_image2, (int(1820), int(1050 - h2/2)), cropped_image2)

                image.paste(cropped_image1, (int(1500 - w1), int(1050 - h1/2)), cropped_image1)


                #centered
                # image.paste(cropped_image2, (int(2349 - w2/2), int(1050 - h2 / 2)), cropped_image2)
                #
                # image.paste(cropped_image1, (int(981 - w1/2), int(1050 - h1 / 2)), cropped_image1)


                image = self.draw_right_side_label(cropped_image2, card_data['reward_name'], image, dark=False)
                image = self.draw_left_side_label(cropped_image1, card_data['challenge_name'], image)
                # image = Image.alpha_composite(image, frame_image)
                # import matplotlib.pyplot as plt
                # plt.imshow(image)
                # plt.show()
            if True:
                # Draw outer border with color based on element
                # border_color = self.element_colors.get(card_data.get('element', 'sol'), colors['border'])
                # draw.rounded_rectangle(
                #     [margin, margin, self.width - margin, self.height - margin],
                #     round(self.width * 0.05),
                #     outline=border_color,
                #     width=self.border_width
                # )


                #Draw top symbol

                # Draw cost shape if cost exists
                circle_radius = round(self.width * 0.064)
                if True:
                    # Center horizontally
                    circle_center_x = self.width / 2
                    # Position vertically so circle center aligns with border
                    circle_center_y = margin + self.border_width / 2 - 45

                    self.draw_view_shape(
                        draw,
                        image,
                        circle_center_x,
                        circle_center_y,
                    )


                # Draw text with fake bold effect
                def draw_semibold_text(text, x, y, font, fill):
                    # Small offset for semi-bold effect (adjust the offset to control boldness)
                    offset = max(1, round(font.size * 0.01))  # Scale with font size

                    # Draw multiple times with small offsets
                    draw.text((x + offset, y), text, font=font, fill=fill)
                    draw.text((x - offset, y), text, font=font, fill=fill)
                    draw.text((x, y + offset), text, font=font, fill=fill)
                    draw.text((x, y - offset), text, font=font, fill=fill)
                    # Finally draw the main text
                    draw.text((x, y), text, font=font, fill=fill)

                # Center cost text in circle with semi-bold effect
                cost_text = str(card_data['foresight'])
                cost_bbox = draw.textbbox((0, 0), cost_text, font=self.cost_font)
                cost_width = cost_bbox[2] - cost_bbox[0]
                cost_height = cost_bbox[3] - cost_bbox[1]
                cost_x = circle_center_x - cost_width / 2
                cost_y = circle_center_y - cost_height / 2 - 65
                draw_semibold_text(
                    cost_text,
                    cost_x,
                    cost_y,
                    self.cost_font,
                    colors['cost']
                )

                self.render_card_sides(draw, card_data, margin, colors)


            return image

        # Save static version (using first frame if animated)
        static_image = render_single_frame(cropped_image1, cropped_image2)

        if is_animated:
            # Save static version
            static_output = os.path.join(output_dir, f"{base_filename}_static.png")
            static_image.save(static_output, 'PNG')

            # Save animated version
            frames = [render_single_frame(frame) for frame in processed_frames]
            frames[0].save(
                output_path,
                save_all=True,
                append_images=frames[1:],
                duration=source_image.info.get('duration', 100),
                loop=0,
                format='GIF'
            )
        else:
            static_image.save(output_path, 'PNG')


    def render_choice_card(self, card_data, processed_frames, image_area, transparent_outside, margin, colors, image_margin, output_dir, base_filename, output_path):
        if 'image' in card_data and card_data['image']:
            try:
                fate_type_map = {
                    "event": "events",
                    "consumable": "consumables",
                    "aspect": "aspects"
                }
                fate_type_path = fate_type_map.get(card_data["fate_type"], "unknown")
                image_path = os.path.join(DOWNLOAD_DIR, fate_type_path, card_data["image"])
                source_image = Image.open(image_path)
                is_animated = hasattr(source_image, 'is_animated') and source_image.is_animated

                # Process first frame to get parameters
                first_frame = source_image.copy()
                if is_animated:
                    first_frame.seek(0)
                processed_frame, crop_params, paste_pos, cropped_images = self.process_frame(first_frame, image_area, card_data=card_data)
                processed_frames.append(processed_frame)

                # Process remaining frames if animated
                if is_animated:
                    for frame_idx in range(1, source_image.n_frames):
                        source_image.seek(frame_idx)
                        frame = source_image.copy()
                        # Use the same processing function with the same parameters
                        processed_frame, _, _, cropped_images = self.process_frame(
                            frame.crop(crop_params),  # Apply initial crop
                            image_area,  # Same target size
                            padding_ratio=0.1,  # Same padding
                            card_data = card_data
                        )
                        processed_frames.append(processed_frame)

            except Exception as e:
                print(f"Error loading image: {e}")




        def render_single_frame(frame_image=None):
            if transparent_outside:
                # Create fully transparent base image
                image = Image.new('RGBA', (self.width, self.height), (0, 0, 0, 0))

                # Create a mask for the rounded rectangle with exact same parameters as border
                mask = Image.new('L', (self.width, self.height), 0)
                mask_draw = ImageDraw.Draw(mask)

                # Use same radius and coordinates as the border
                corner_radius = round(self.width * 0.05)
                rect_bounds = [margin, margin, self.width - margin, self.height - margin]

                # Draw the mask slightly smaller than the border to prevent bleeding
                inset = self.border_width // 2
                mask_bounds = [
                    rect_bounds[0] + inset,
                    rect_bounds[1] + inset,
                    rect_bounds[2] - inset,
                    rect_bounds[3] - inset
                ]
                mask_draw.rounded_rectangle(
                    mask_bounds,
                    corner_radius,
                    fill=255
                )

                # Create background color layer
                bg_layer = Image.new('RGBA', (self.width, self.height), colors['background'])
                # Apply the mask to the background
                bg_layer.putalpha(mask)

                # Composite the background onto the transparent base
                image = Image.alpha_composite(image, bg_layer)
            else:
                # Create fully transparent base image
                image = Image.new('RGBA', (self.width, self.height), (0, 0, 0, 0))

                # Create a mask for the rounded rectangle with exact same parameters as border
                mask = Image.new('L', (self.width, self.height), 0)
                mask_draw = ImageDraw.Draw(mask)

                # Use same radius and coordinates as the border
                corner_radius = round(self.width * 0.05)
                rect_bounds = [margin/3.2, margin/3.2, self.width - margin/3.2, self.height - margin/3.2]

                # Draw the mask slightly smaller than the border to prevent bleeding
                inset = self.border_width // 2
                mask_bounds = [
                    rect_bounds[0] + inset,
                    rect_bounds[1] + inset,
                    rect_bounds[2] - inset,
                    rect_bounds[3] - inset
                ]
                mask_draw.rounded_rectangle(
                    mask_bounds,
                    corner_radius,
                    fill=255
                )

                # Create background color layer
                bg_layer = Image.new('RGBA', (self.width, self.height), colors['background'])
                # Apply the mask to the background
                bg_layer.putalpha(mask)

                # Composite the background onto the transparent base
                image = Image.alpha_composite(image, bg_layer)

            draw = ImageDraw.Draw(image)

            # Place processed image if available
            if frame_image:
                # Convert frame_image back to RGBA if it's not already
                if frame_image.mode != 'RGBA':
                    frame_image = frame_image.convert('RGBA')
                # import matplotlib.pyplot as plt
                # plt.imshow(frame_image)
                # plt.show()
                # Simple paste without using the image as its own mask
                image.paste(frame_image, (image_margin, image_margin), frame_image)

                if 'consumable' in card_data['fate_type'].lower():

                    image = self.draw_right_side_label(cropped_images, card_data['name'], image)

                elif card_data['fate_type'].lower() == 'ritual':
                    image = self.draw_right_side_label(cropped_images, card_data['reward_name'], image)
                    image = self.draw_left_side_label(cropped_images, card_data['challenge_name'], image)
                # image = Image.alpha_composite(image, frame_image)
                # import matplotlib.pyplot as plt
                # plt.imshow(image)
                # plt.show()
            if True:
                # Draw outer border with color based on element
                # border_color = self.element_colors.get(card_data.get('element', 'sol'), colors['border'])
                # draw.rounded_rectangle(
                #     [margin, margin, self.width - margin, self.height - margin],
                #     round(self.width * 0.05),
                #     outline=border_color,
                #     width=self.border_width
                # )


                #Draw top symbol

                # Draw cost shape if cost exists
                circle_radius = round(self.width * 0.064)
                if 'foresight' in card_data and card_data['foresight'] is not None:
                    # Center horizontally
                    circle_center_x = self.width / 2
                    # Position vertically so circle center aligns with border
                    circle_center_y = margin + self.border_width / 2 - 45

                    self.draw_view_shape(
                        draw,
                        image,
                        circle_center_x,
                        circle_center_y,
                    )


                # Draw text with fake bold effect
                def draw_semibold_text(text, x, y, font, fill):
                    # Small offset for semi-bold effect (adjust the offset to control boldness)
                    offset = max(1, round(font.size * 0.01))  # Scale with font size

                    # Draw multiple times with small offsets
                    draw.text((x + offset, y), text, font=font, fill=fill)
                    draw.text((x - offset, y), text, font=font, fill=fill)
                    draw.text((x, y + offset), text, font=font, fill=fill)
                    draw.text((x, y - offset), text, font=font, fill=fill)
                    # Finally draw the main text
                    draw.text((x, y), text, font=font, fill=fill)

                # Center cost text in circle with semi-bold effect
                cost_text = str(card_data['foresight'])
                cost_bbox = draw.textbbox((0, 0), cost_text, font=self.cost_font)
                cost_width = cost_bbox[2] - cost_bbox[0]
                cost_height = cost_bbox[3] - cost_bbox[1]
                cost_x = circle_center_x - cost_width / 2
                cost_y = circle_center_y - cost_height / 2 - 65
                draw_semibold_text(
                    cost_text,
                    cost_x,
                    cost_y,
                    self.cost_font,
                    colors['cost']
                )

                # Draw card name with auto-scaling
                name_text = card_data['name']

                # Calculate maximum allowed width (with padding)
                max_title_width = self.width - (2 * margin) - (4 * self.border_width) - 2 * self.px_per_mm * 3

                # Start with default font size and try progressively smaller sizes
                base_size = round(self.width / 15)
                min_size = round(base_size * 0.5)
                current_font = None

                # Try sizes from largest to smallest until finding one that fits
                for size in range(base_size, min_size - 1, -1):
                    test_font = ImageFont.truetype(FONT_PATH, size)
                    test_bbox = draw.textbbox((0, 0), name_text, font=test_font)
                    test_width = test_bbox[2] - test_bbox[0]

                    if test_width <= max_title_width:
                        current_font = test_font
                        break

                # If no suitable size found, use minimum size
                if current_font is None:
                    current_font = ImageFont.truetype(FONT_PATH, min_size)

                # Draw card name with semibold effect
                name_bbox = draw.textbbox((0, 0), name_text, font=current_font)
                name_width = name_bbox[2] - name_bbox[0]
                name_x = (self.width - name_width) / 2
                name_y = self.height * 0.7
                draw_semibold_text(
                    name_text,
                    name_x,
                    name_y,
                    current_font,
                    colors['text']
                )

                if 'text' in card_data and card_data['text']:
                    text = card_data['text']

                    # Calculate the maximum width for the text
                    text_box_width = self.width - (2 * margin) - (4 * self.border_width) - 2 * self.px_per_mm * 3
                    text_box_height = self.height * 0.3  # Height of the text box

                    # Use the original text_x and text_y positions
                    text_bbox = draw.textbbox((0, 0), text, font=self.text_font)
                    text_width = text_bbox[2] - text_bbox[0]
                    text_x = self.width / 2  # Center position
                    text_y = self.height * 0.85  # Original vertical position

                    # Draw the wrapped and scaled text at the specified position
                    self.draw_wrapped_text(
                        draw,
                        text,
                        text_x,
                        text_y,
                        text_box_width,
                        text_box_height,
                        self.text_font.size,
                        FONT_PATH,
                        colors['text']
                    )

            return image

        # Save static version (using first frame if animated)
        static_image = render_single_frame(processed_frames[0] if processed_frames else None)

        if is_animated:
            # Save static version
            static_output = os.path.join(output_dir, f"{base_filename}_static.png")
            static_image.save(static_output, 'PNG')

            # Save animated version
            frames = [render_single_frame(frame) for frame in processed_frames]
            frames[0].save(
                output_path,
                save_all=True,
                append_images=frames[1:],
                duration=source_image.info.get('duration', 100),
                loop=0,
                format='GIF'
            )
        else:
            static_image.save(output_path, 'PNG')


    def render_card(self, card_data, output_dir="output", transparent_outside=False):
        """Render a card and save it along with its data.

        Args:
            card_data (dict): Card data including name, cost, type, etc.
            output_dir (str): Directory to save the output files
            transparent_outside (bool): If True, only fill background within the rounded rectangle
        """
        # Create output directory if it doesn't exist
        os.makedirs(output_dir, exist_ok=True)


        # Generate base filename from card name
        if "type" in card_data and card_data["type"] == "ritual":
            structure_type = "ritual"
            # For rituals, use the challenge_side name
            base_filename = card_data["challenge_side"]["name"].lower().replace(" ", "_")
            is_dark_mode = True
            colors = self.dark_mode if is_dark_mode else self.light_mode
        else:
            structure_type = "card"
            base_filename = card_data["bonus_side"]["name"].lower().replace(" ", "_")
            is_dark_mode = "Arcana" in card_data.get('type', '').split()
            colors = self.dark_mode if is_dark_mode else self.light_mode

        # base_filename = card_data['name'].lower().replace(' ', '_')
        image_filename = f"{base_filename}.png"
        json_filename = f"{base_filename}.json"
        output_path = os.path.join(output_dir, image_filename)
        json_path = os.path.join(output_dir, json_filename)

        # Determine color scheme


        # Calculate image area
        border_width = round(self.px_per_mm * 0.5)
        margin = round(self.px_per_mm * self.bleed_mm)
        image_margin = margin + border_width * 2

        image_area = (
            self.width - (image_margin * 2),
            self.height - (image_margin * 2)
        )

        # Process image if provided
        processed_frames = []
        crop_params = None
        paste_pos = None
        is_animated = False

        if structure_type == 'card':
            self.render_choice_card(card_data, processed_frames, image_area, transparent_outside, margin, colors,
                               image_margin, output_dir, base_filename, output_path)
        else:
            self.render_ritual_card(card_data, processed_frames, image_area, transparent_outside, margin, colors,
                               image_margin, output_dir, base_filename, output_path)


    def render_fate(self, card_data, output_dir="output", transparent_outside=False):
        """Render a card and save it along with its data.

        Args:
            card_data (dict): Card data including name, cost, type, etc.
            output_dir (str): Directory to save the output files
            transparent_outside (bool): If True, only fill background within the rounded rectangle
        """
        # Create output directory if it doesn't exist
        os.makedirs(output_dir, exist_ok=True)


        # Generate base filename from card name
        base_filename = card_data["name"].lower().replace(" ", "_")
        is_dark_mode = "Arcana" in card_data.get('type', '').split()
        colors = self.dark_mode if is_dark_mode else self.light_mode

        # base_filename = card_data['name'].lower().replace(' ', '_')
        image_filename = f"{base_filename}.png"
        json_filename = f"{base_filename}.json"
        output_path = os.path.join(output_dir, image_filename)
        json_path = os.path.join(output_dir, json_filename)

        # Determine color scheme


        # Calculate image area
        border_width = round(self.px_per_mm * 0.5)
        margin = round(self.px_per_mm * self.bleed_mm)
        image_margin = margin + border_width * 2

        image_area = (
            self.width - (image_margin * 2),
            self.height - (image_margin * 2)
        )

        # Process image if provided
        processed_frames = []
        crop_params = None
        paste_pos = None
        is_animated = False

        self.render_choice_card(card_data, processed_frames, image_area, transparent_outside, margin, colors,
                               image_margin, output_dir, base_filename, output_path)

        return output_path


    def render_ritual(self, card_data, output_dir="output", transparent_outside=False):
        """Render a card and save it along with its data.

        Args:
            card_data (dict): Card data including name, cost, type, etc.
            output_dir (str): Directory to save the output files
            transparent_outside (bool): If True, only fill background within the rounded rectangle
        """
        # Create output directory if it doesn't exist
        os.makedirs(output_dir, exist_ok=True)


        # Generate base filename from card name
        # For rituals, use the challenge_side name
        base_filename = card_data["challenge_name"].lower().replace(" ", "_")
        is_dark_mode = True
        colors = self.dark_mode if is_dark_mode else self.light_mode

        # base_filename = card_data['name'].lower().replace(' ', '_')
        image_filename = f"{base_filename}.png"
        json_filename = f"{base_filename}.json"
        output_path = os.path.join(output_dir, image_filename)
        json_path = os.path.join(output_dir, json_filename)

        # Determine color scheme


        # Calculate image area
        border_width = round(self.px_per_mm * 0.5)
        margin = round(self.px_per_mm * self.bleed_mm)
        image_margin = margin + border_width * 2

        image_area = (
            self.width - (image_margin * 2),
            self.height - (image_margin * 2)
        )

        # Process image if provided
        processed_frames = []
        crop_params = None
        paste_pos = None
        is_animated = False

        self.render_ritual_card(card_data, processed_frames, image_area, transparent_outside, margin, colors,
                               image_margin, output_dir, base_filename, output_path)

        return output_path

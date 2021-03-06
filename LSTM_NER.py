import argparse
import json
import os
import pickle
import zipfile
from itertools import chain

import keras.backend.tensorflow_backend as KTF
import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas
import seaborn as sns
import tensorflow as tf
from tensorflow.python.keras import Sequential
from tensorflow.python.keras.layers import Bidirectional, Embedding, Dropout, SpatialDropout1D, Dense, LSTM, \
    BatchNormalization
from tensorflow.python.keras.optimizer_v2.adam import Adam
from tensorflow.python.keras.utils.vis_utils import plot_model
from tensorflow.python.ops.init_ops import Constant
from torchnlp.download import requests
from tqdm import trange, tqdm

# Set up a argument parser
parser = argparse.ArgumentParser()
parser.add_argument("--gpu", type=int, default=0, required=False, help="The number of gpu you want to use")
parser.add_argument("--epoch", type=int, default=300, required=False)
parser.add_argument("--bs", type=int, default=64, required=False)
parser.add_argument("--lr", type=float, default=0.001, required=False)
parser.add_argument("--glove_size", type=int, choices=[6, 42, 840], default=840, required=False,
                    help="the number of how many billion words does the glove model pretrained on")
parser.add_argument("--model", type=str, choices=["lstm_bilstm", "bilstm", "bilstm_bilstm"], default="bilstm_bilstm",
                    required=False, help="The model to train the NER")
parser.add_argument("--layers", type=int, default=2, required=False, help="The number of BiLSTM layers you want to try")
args = parser.parse_args()
print(args)

# Set the GPU
os.environ["CUDA_VISIBLE_DEVICES"] = "{}".format(args.gpu)
config = tf.compat.v1.ConfigProto()
config.gpu_options.per_process_gpu_memory_fraction = 0.95
config.gpu_options.allow_growth = True  # Comment out this line if you are not using RTX 2070 or 2080, if it still
# doesn't work then uncomment this line
sess = tf.compat.v1.Session(config=config)
KTF.set_session(sess)

# Check the existence of Glove and Download it
glove_6B = 'http://downloads.cs.stanford.edu/nlp/data/glove.6B.zip'
glove_42B = 'http://downloads.cs.stanford.edu/nlp/data/glove.42B.300d.zip'
glove_840B = 'http://downloads.cs.stanford.edu/nlp/data/glove.840B.300d.zip'
if not os.path.exists('./glove'):
    os.makedirs('./glove')
if not os.path.isfile('./glove/glove.6B.zip'):
    print("\nDownloading glove.6B, please wait.\n")
    r_6b = requests.get(glove_6B, allow_redirects=True)
    open('./glove/glove.6B.zip', 'wb').write(r_6b.content)
    print("Downloading glove.6B Finished\n")
    with zipfile.ZipFile('./glove/glove.6B.zip', 'r') as zip_ref:
        zip_ref.extractall(path="./glove/")
    print("Glove 6B extracted")

if not os.path.isfile('./glove/glove.42B.300d.zip'):
    print("\nDownloading glove.42B, please wait.\n")
    r_42b = requests.get(glove_42B, allow_redirects=True)
    open('./glove/glove.42B.300d.zip', 'wb').write(r_42b.content)
    print("Downloading glove.42B Finished\n")
    with zipfile.ZipFile('./glove/glove.42B.300d.zip', 'r') as zip_ref:
        zip_ref.extractall(path="./glove/")
    print("Glove 42B extracted")

if not os.path.isfile('./glove/glove.840B.300d.zip'):
    print("\nDownloading glove.840B, please wait.\n")
    r_840b = requests.get(glove_840B, allow_redirects=True)
    open('./glove/glove.840B.300d.zip', 'wb').write(r_840b.content)
    print("Downloading glove.840B Finished\n")
    with zipfile.ZipFile('./glove/glove.840B.300d.zip', 'r') as zip_ref:
        zip_ref.extractall(path="./glove/")
    print("Glove 840B extracted")

# Set up some parameter we can use
glove_path_dict = {6: './glove/glove.6B.100d.txt', 42: './glove/glove.42B.300d.txt', 840: './glove/glove.840B.300d.txt'}
glove_dimension_dict = {6: 100, 42: 300, 840: 300}
epochs = args.epoch
BS = args.bs
LR = args.lr
glove_path = glove_path_dict[args.glove_size]
glove_dimension = glove_dimension_dict[args.glove_size]

# Load the data for three splits
train_dict = pickle.load(open("./data/train.pkl", 'rb'))
val_dict = pickle.load(open("./data/val.pkl", 'rb'))
test_dict = pickle.load(open("./data/test.pkl", 'rb'))

# Read the glove embedding from the txt and save it into a dict
glove_dict = {}
with open(glove_path, 'r', encoding="utf-8") as f:
    for line in tqdm(f):
        values = line.split(' ')
        word = values[0]
        vector = np.asarray(values[1:], "float32")
        glove_dict[word] = vector

# Give all the words appeared in our corpus their glove embedding, for those who are not exist, random initialize them
encoded_dict = {}
count = 0
total = 0
glove_keys = glove_dict.keys()
for i in [train_dict, val_dict, test_dict]:
    for j in trange(len(i['word_seq'])):
        for word in i['word_seq'][j]:
            if word not in glove_keys:
                encoded_dict[word] = np.random.rand(1, glove_dimension)[0]
                count += 1
                total += 1
            else:
                encoded_dict[word] = glove_dict[word]
                total += 1
# Test how many words are found in glove and how many are randomly initialized
print("words not found {}".format(count))
print("words total {}".format(total))
print(len(encoded_dict))
np.save("./glove/encoded_dict_{}B_{}d.npy".format(args.glove_size, glove_dimension), encoded_dict)

# Build a dict that records the word to a single unique integer, and our encoded matrix for word embedding
encoded_word2id = {}
encoded_matrix = np.zeros((len(encoded_dict.keys()), glove_dimension), dtype=float)
for i, word in enumerate(encoded_dict.keys()):
    encoded_word2id[word] = i
    encoded_matrix[i] = encoded_dict[word]
print(encoded_matrix.shape)
np.save("./glove/encoded_matrix_{}B_{}d.npy".format(args.glove_size, glove_dimension), encoded_matrix)

# Build the tag <--> index dictionary and add PAD tag into it
tag_list = list(set(chain(*train_dict["tag_seq"])))
tag_to_index_dict = {t: i for i, t in enumerate(tag_list)}
index_to_tag_dict = {i: t for i, t in enumerate(tag_list)}

# save out dictionary for generation
if not os.path.exists('./lstm_model'):
    os.mkdir('./lstm_model/')
if not os.path.exists('./lstm_results'):
    os.mkdir('./lstm_results')
np.save("./lstm_model/model_tag2id_e{}_bs{}.npy".format(epochs, BS), tag_to_index_dict)
np.save("./lstm_model/model_id2tag_e{}_bs{}.npy".format(epochs, BS), index_to_tag_dict)

# Load some parameters for deep learning
embedding_dim = glove_dimension
num_words = len(encoded_dict)
input_length = 128
n_tags = len(tag_to_index_dict)
print(embedding_dim, num_words, input_length, n_tags)


# Set our model
def get_bi_lstm_model():
    model = Sequential()
    model.add(
        Embedding(num_words, embedding_dim, embeddings_initializer=Constant(encoded_matrix), input_length=input_length,
                  trainable=True))
    model.add(SpatialDropout1D(0.2))
    model.add(BatchNormalization())
    if args.model == "lstm_bilstm":
        model.add(LSTM(128, return_sequences=True))
    elif args.model == "bilstm_bilstm":
        for _ in range(args.layers):
            model.add(Bidirectional(LSTM(128, return_sequences=True)))
            model.add(BatchNormalization())
            model.add(Dropout(0.3))
    adam = Adam(lr=LR, beta_1=0.9, beta_2=0.999)
    model.add(Dense(units=n_tags, activation='softmax'))
    model.compile(loss='sparse_categorical_crossentropy', optimizer=adam, metrics=['accuracy'])
    model.summary()
    return model


# Define the function to train our model
def train_model(X, y, val_X, val_y, model):
    hist = model.fit(X, y, batch_size=BS, verbose=1, epochs=epochs, validation_data=(val_X, val_y), shuffle=True)
    return hist


# build our model and print the summary
model_bi_lstm_lstm = get_bi_lstm_model()
try:
    plot_model(model_bi_lstm_lstm, show_shapes=True)
except ImportError and Exception:
    pass

# Use the dict we've prepared before to do the embedding and transformation
train_input = np.array(
    [[encoded_word2id[word] for word in train_dict['word_seq'][i]] for i in range(len(train_dict['word_seq']))])
val_input = np.array(
    [[encoded_word2id[word] for word in val_dict['word_seq'][i]] for i in range(len(val_dict['word_seq']))])
test_input = np.array(
    [[encoded_word2id[word] for word in test_dict['word_seq'][i]] for i in range(len(test_dict['word_seq']))])
train_output = np.array(
    [[tag_to_index_dict[tag] for tag in train_dict['tag_seq'][i]] for i in range(len(train_dict['tag_seq']))])
val_output = np.array(
    [[tag_to_index_dict[tag] for tag in val_dict['tag_seq'][i]] for i in range(len(val_dict['tag_seq']))])

# Check the shape of our input, their first dimension must be the same
print(train_input.shape, val_input.shape, test_input.shape)
print(train_output.shape, val_output.shape)

# Train our model and save the loss recording
history = train_model(train_input, train_output, val_input, val_output, model_bi_lstm_lstm)

# Do some visualization
sns.set_style(style="darkgrid")
sns.set(font_scale=1.75)
plt.rcParams["figure.figsize"] = (30, 15)

mpl.use('Agg')
plt.plot(history.history['accuracy'])
plt.plot(history.history['val_accuracy'])
plt.title('model accuracy')
plt.ylabel('accuracy')
plt.xlabel('epoch')
plt.legend(['train', 'validation'], loc='upper left')
# plt.show()
plt.savefig(
    './lstm_results/accuracy_BS{}E{}LR{}_{}B_{}d_{}layer.png'.format(BS, epochs, LR, args.glove_size, glove_dimension,
                                                                     args.layers))
plt.clf()
plt.plot(history.history['loss'])
plt.plot(history.history['val_loss'])
plt.title('model loss')
plt.ylabel('loss')
plt.xlabel('epoch')
plt.legend(['train', 'validation'], loc='upper left')
# plt.show()
plt.savefig(
    './lstm_results/model_loss_BS{}E{}LR{}_{}B_{}d_{}layers.png'.format(BS, epochs, LR, args.glove_size,
                                                                        glove_dimension, args.layers))

# Save the validation accuracy for us to find the best model trained
np.save(
    './lstm_results/model_results_val_BS{}E{}LR{}_{}B_{}d_{}layers.npy'.format(BS, epochs, LR, args.glove_size,
                                                                               glove_dimension, args.layers),
    history.history['val_accuracy'])

# Save our trained model and open up a answer csv, initialize all the id
model_bi_lstm_lstm.save(
    './lstm_model/model_BS{}E{}LR{}_{}B_{}d_{}layers.pkl'.format(BS, epochs, LR, args.glove_size, glove_dimension,
                                                                 args.layers))
answer = pandas.DataFrame(columns=['id', 'labels'])
answer['id'] = test_dict['id']

# Predict on the test dict and save it to answer csv file
predict = model_bi_lstm_lstm.predict(test_input)
for i in range(len(answer)):
    sentence_tag = []
    for j in range(128):
        tag = index_to_tag_dict[np.argmax(predict[i][j])]
        sentence_tag.append(tag)
    answer.loc[i, 'labels'] = json.dumps(sentence_tag)
answer.to_csv(
    './lstm_results/answer_BS{}E{}LR{}_{}B_{}d_{}layers.csv'.format(BS, epochs, LR, args.glove_size, glove_dimension,
                                                                    args.layers), index=True)
